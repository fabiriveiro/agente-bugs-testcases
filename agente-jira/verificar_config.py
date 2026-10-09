"""Verifica contra Jira que la configuración del agente sea válida (solo lectura, no modifica nada).

Uso:
    python verificar_config.py MAADM-9252

El ticket de ejemplo puede ser un bug existente o una historia del proyecto donde se crean los bugs.
"""

import asyncio
import sys

import httpx

import agente_jira as a

OK, WARN, FAIL = "✅", "⚠️ ", "❌"
problems = 0


def report(status: str, text: str) -> None:
    global problems
    if status == FAIL:
        problems += 1
    print(f"{status} {text}")


async def main(issue_key: str) -> None:
    jira = a.JiraRest()
    project_key = issue_key.split("-")[0]
    print(f"Verificando configuración en {a.os.environ['JIRA_URL']} con el ticket {issue_key}\n")

    # 1. Acceso y ticket de ejemplo
    try:
        me = await jira._get("/rest/api/3/myself")
        report(OK, f"Acceso a Jira como {me.get('displayName')}")
    except httpx.HTTPStatusError as e:
        report(FAIL, f"No se pudo acceder a Jira (HTTP {e.response.status_code}). Revisá JIRA_USERNAME / JIRA_API_TOKEN "
                     "o si tu IP está permitida.")
        return
    issue = await jira._get(f"/rest/api/3/issue/{issue_key}", expand="names")
    names, fields = issue.get("names", {}), issue["fields"]
    print(f"\n--- Ticket {issue_key}: [{fields['issuetype']['name']}] {fields['summary']}")
    for fid in ("issuetype", "priority", "components", "parent", "issuelinks"):
        value = fields.get(fid)
        if fid == "issuelinks":
            value = [f"{l['type']['name']} → {(l.get('outwardIssue') or l.get('inwardIssue'))['key']}" for l in value or []]
        elif fid == "parent" and value:
            value = f"{value['key']} ({value['fields']['issuetype']['name']}: {value['fields']['summary']})"
        print(f"    {names.get(fid, fid)}: {a._display(value) if value else '(vacío)'}")
    for fid, value in fields.items():
        if fid.startswith("customfield_") and _matches_config(names.get(fid, "")):
            print(f"    {names[fid]} ({fid}): {a._display(value) if value else '(vacío)'}")

    # 2. Título con el formato "CLAVE | ..."
    print("\n--- Reglas del agente")
    if fields["issuetype"]["name"].lower() in {"bug", "error", "defecto"}:
        if a.STORY_KEY_RE.match(fields["summary"]):
            report(OK, "El título sigue el formato 'CLAVE | Título de la historia'")
        else:
            report(WARN, "El título del ticket de ejemplo no empieza con 'CLAVE |'")

    # 3. Tipo de ticket y pantalla de creación
    try:
        create_fields = await jira.create_fields(project_key, a.BUG_ISSUE_TYPE)
        report(OK, f"El proyecto {project_key} permite crear tickets tipo '{a.BUG_ISSUE_TYPE}'")
    except RuntimeError as e:
        report(FAIL, str(e))
        create_fields = []
    by_id = {f["fieldId"]: f for f in create_fields}

    # 4. Componentes
    components = {c["name"]: c for c in await jira._get(f"/rest/api/3/project/{project_key}/components")}
    for name in [c.strip() for c in a.BUG_COMPONENTS.split(",") if c.strip()]:
        if name in components:
            report(OK, f"Componente '{name}' existe en {project_key}")
        else:
            close = [c for c in components if c.lower() == name.lower()]
            hint = f" (en Jira se llama '{close[0]}')" if close else f" (disponibles: {', '.join(components) or 'ninguno'})"
            report(FAIL, f"Componente '{name}' no existe en {project_key}{hint}. Corregí JIRA_BUG_COMPONENTS.")
    if create_fields and "components" not in by_id:
        report(FAIL, f"El campo Componentes no está en la pantalla de creación de {a.BUG_ISSUE_TYPE}")

    # 5. Principal (parent)
    if create_fields:
        report(OK if "parent" in by_id else WARN,
               f"El campo Principal (parent) está disponible al crear un {a.BUG_ISSUE_TYPE}" if "parent" in by_id
               else f"El campo Principal (parent) no aparece en la pantalla de creación de {a.BUG_ISSUE_TYPE}")

    # 6. Campos copiados de la historia
    try:
        ids = await jira.field_ids(a.COPY_FIELDS)
        for name, fid in ids.items():
            report(OK, f"Campo a copiar '{name}' existe ({fid})")
            if create_fields and fid not in by_id:
                report(FAIL, f"'{name}' no está en la pantalla de creación de {a.BUG_ISSUE_TYPE}: Jira rechazaría el valor copiado")
    except RuntimeError as e:
        report(FAIL, str(e))

    # 7. Campos que se piden con opciones
    for wanted in a.ASK_FIELDS:
        field = next((f for f in create_fields if wanted in (f.get("fieldId"), f.get("key"))
                      or a._normalize(f.get("name", "")) == a._normalize(wanted)), None)
        if not field:
            report(FAIL, f"Campo a pedir '{wanted}' no está en la pantalla de creación de {a.BUG_ISSUE_TYPE}. Revisá JIRA_ASK_FIELDS.")
            continue
        options = [a._display(o) for o in field.get("allowedValues") or []]
        if options:
            report(OK, f"Campo a pedir '{field['name']}' ({field['fieldId']}) con {len(options)} opciones: {', '.join(options)}")
        else:
            report(FAIL, f"'{field['name']}' no tiene opciones para elegir")

    # 8. Campos obligatorios que el agente no completa
    handled = {"project", "issuetype", "summary", "description", "components", "parent", "reporter", "assignee"}
    handled |= {f["fieldId"] for f in create_fields if f["fieldId"] in (await _configured_ids(jira, create_fields))}
    for f in create_fields:
        if f.get("required") and not f.get("hasDefaultValue") and f["fieldId"] not in handled:
            report(FAIL, f"Campo obligatorio '{f['name']}' ({f['fieldId']}) que el agente no completa")

    # 9. Tipo de vínculo
    link_types = [t["name"] for t in (await jira._get("/rest/api/3/issueLinkType"))["issueLinkTypes"]]
    report(OK if a.LINK_TYPE in link_types else FAIL,
           f"Tipo de vínculo {a.LINK_TYPE!r} existe" if a.LINK_TYPE in link_types
           else f"No existe el vínculo {a.LINK_TYPE!r} (disponibles: {', '.join(link_types)})")

    # 10. Casos de prueba
    print("\n--- Casos de prueba")
    try:
        test_fields = {f["fieldId"]: f for f in await jira.create_fields(project_key, a.TEST_ISSUE_TYPE)}
        report(OK, f"El proyecto {project_key} permite crear tickets tipo '{a.TEST_ISSUE_TYPE}'")
        report(OK if "parent" in test_fields else FAIL, f"Principal (parent) disponible al crear un {a.TEST_ISSUE_TYPE}")
        priorities = [o.get("name", "") for o in test_fields.get("priority", {}).get("allowedValues", [])]
        has_priority = any(a._normalize(p) == a._normalize(a.TEST_PRIORITY) for p in priorities)
        report(OK if has_priority else FAIL, f"Prioridad '{a.TEST_PRIORITY}' para {a.TEST_ISSUE_TYPE}"
               + ("" if has_priority else f" no existe (disponibles: {', '.join(priorities)}). Revisá JIRA_TEST_PRIORITY."))
        for name, fid in (await jira.field_ids(a.COPY_FIELDS)).items():
            report(OK if fid in test_fields else FAIL, f"'{name}' disponible al crear un {a.TEST_ISSUE_TYPE}")
    except RuntimeError as e:
        report(FAIL, str(e))

    print(f"\n{'Todo OK.' if not problems else f'{problems} problema(s) para corregir.'}")


def _matches_config(name: str) -> bool:
    wanted = [a._normalize(n) for n in a.COPY_FIELDS + a.ASK_FIELDS]
    return a._normalize(name) in wanted


async def _configured_ids(jira: a.JiraRest, create_fields: list[dict]) -> set[str]:
    ids = set()
    try:
        ids |= set((await jira.field_ids(a.COPY_FIELDS)).values())
    except RuntimeError:
        pass
    for wanted in a.ASK_FIELDS:
        ids |= {f["fieldId"] for f in create_fields
                if wanted in (f.get("fieldId"), f.get("key")) or a._normalize(f.get("name", "")) == a._normalize(wanted)}
    return ids


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        sys.exit("Uso: python verificar_config.py CLAVE-123")
    asyncio.run(main(sys.argv[1].rstrip("/").split("/")[-1]))
