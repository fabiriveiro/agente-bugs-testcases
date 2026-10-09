"""Agente de bugs para Jira.

Se conecta al servidor MCP de Atlassian (mcp-atlassian) usando tu email + API token
y usa un modelo de IA (Gemini por defecto, Claude opcional) para:
  1. Crear tickets de Bug bien redactados a partir de lo que le describas.
  2. Generar reportes de bugs existentes (por proyecto, sprint, fechas, prioridad...).

Uso:
    python agente_jira.py
"""

import asyncio
import json
import os
import re
import shutil
import sys
import unicodedata
from datetime import date
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

load_dotenv()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
REPORTS_DIR = Path(__file__).parent / "reportes"
# Template de bugs del equipo: editá este archivo para cambiar cómo se redactan los tickets.
BUG_TEMPLATE = (Path(__file__).parent / "template_bug.md").read_text(encoding="utf-8").strip()

# Herramientas del MCP que el agente puede usar. Todo lo demás queda deshabilitado
# en el propio servidor (no puede borrar, transicionar ni editar tickets).
JIRA_TOOLS = [
    "jira_search",
    "jira_get_issue",
    "jira_get_all_projects",
    "jira_get_project_issues",
    "jira_search_fields",
    "jira_get_project_components",
    "jira_get_link_types",
    "jira_create_issue",
    "jira_create_issue_link",
]
# Componentes que se asignan siempre a todos los bugs (nombres exactos como figuran en Jira).
# Tipo de ticket con el que se crean los bugs (en MAADM se llama "Error").
BUG_ISSUE_TYPE = os.getenv("JIRA_BUG_ISSUE_TYPE", "Error")
BUG_COMPONENTS = os.getenv("JIRA_BUG_COMPONENTS", "Frontend,Backend")
# Campos que se copian automáticamente de la historia vinculada al bug (nombres como figuran en Jira).
COPY_FIELDS = [f.strip() for f in os.getenv("JIRA_COPY_FIELDS", "Área solicitante").split(",") if f.strip()]
# Campos que se le piden al usuario al crear cada bug, mostrando las opciones válidas de Jira
# ("priority" es el ID de Prioridad; el resto se busca por nombre).
ASK_FIELDS = [f.strip() for f in os.getenv("JIRA_ASK_FIELDS", "priority,Ambiente detectado").split(",") if f.strip()]
# Tipo de vínculo entre el bug y su historia: el bug "blocks" la historia (como MAADM-9252 → MAADM-6820).
LINK_TYPE = os.getenv("JIRA_LINK_TYPE", "Blocks")
STORY_KEY_RE = re.compile(r"^\s*([A-Z][A-Z0-9]+-\d+)\s*\|")

# Casos de prueba: tickets TEST con Principal = la historia (como MAADM-8748 → MAADM-6820).
TEST_ISSUE_TYPE = os.getenv("JIRA_TEST_ISSUE_TYPE", "TEST")
TEST_PRIORITY = os.getenv("JIRA_TEST_PRIORITY", "Media")
CA_RE = re.compile(r"\bCA-(\d{1,2})\b")
HU_LINE_RE = re.compile(r"^\*\*Historia de Usuario:\*\*.*$", re.MULTILINE)
TEST_TEMPLATE = (Path(__file__).parent / "template_caso_prueba.md").read_text(encoding="utf-8").strip()
# Herramientas que escriben en Jira: piden confirmación por consola antes de ejecutarse.
WRITE_TOOLS = {"jira_create_issue", "jira_create_issue_link"}

SYSTEM_PROMPT = f"""Sos un asistente de QA que trabaja sobre Jira. Hoy es {date.today().isoformat()}.
Proyecto por defecto: {os.getenv("JIRA_DEFAULT_PROJECT") or "(ninguno; preguntalo si hace falta)"}.

## Crear bugs
Cuando el usuario describa un problema, redactá el Bug siguiendo exactamente este template del equipo
(respetá sus secciones, su orden y sus títulos; la descripción va en Markdown):

<template_bug>
{BUG_TEMPLATE}
</template_bug>

No inventes datos: si falta información que el template pide (pasos, entorno, proyecto), preguntala.

Campos de Jira que todo bug debe llevar (además del título y la descripción):
- Componente: todos los bugs llevan {BUG_COMPONENTS} más los componentes que tenga la historia vinculada (el sistema
  los asigna automáticamente al crear el ticket; no hace falta preguntarlos).
- Principal (campo "parent" de Jira): consultá la historia con jira_get_issue pidiendo los campos summary, parent y project,
  y proponé el mismo principal que tiene la historia (normalmente su épica).
  Si la historia no tiene principal, preguntale al usuario cuál va. Nunca lo inventes.
- {", ".join(COPY_FIELDS)}: el sistema lo copia automáticamente de la historia vinculada al crear el ticket
  (la toma de la clave al inicio del título); no hace falta preguntarlo ni incluirlo en additional_fields.
- Prioridad y Ambiente detectado: al crear el ticket, el sistema le muestra al usuario las opciones válidas de Jira
  y le pide que elija. No los preguntes vos ni los incluyas en additional_fields.
- En el borrador mostrá explícitamente Título, Componente y Principal para que el usuario los confirme o corrija,
  y avisale que al confirmar se le van a pedir Prioridad y Ambiente detectado.

Antes de crear el ticket, buscá con jira_search si ya existe un bug parecido y avisá si hay posibles duplicados.
Cuando el usuario apruebe el borrador:
1. Creá el ticket con jira_create_issue: issue_type "{BUG_ISSUE_TYPE}", components "{BUG_COMPONENTS}",
   y additional_fields con el principal, por ejemplo {{"parent": "MAADM-1234"}}.
2. Vinculá el bug con la historia usando jira_create_issue_link: link_type "{LINK_TYPE}" (el bug bloquea la historia),
   inward_issue_key = clave del bug nuevo, outward_issue_key = clave de la historia.
3. Informá la clave del bug creado y confirmá que quedó vinculado. Si algún paso falla, explicá el error y no lo reintentes
   sin consultar al usuario.

## Casos de prueba
Cuando el usuario pase una historia (clave como MAADM-6820 o link como https://fpatronal.atlassian.net/browse/MAADM-6820)
y pida casos de prueba:
1. Leé la historia con jira_get_issue usando fields "*all" para ver el resumen, la descripción, los criterios de
   aceptación (pueden estar en la descripción o en campos personalizados) y cualquier detalle funcional.
2. Buscá los casos existentes con jira_search, JQL: parent = <CLAVE> AND issuetype = "{TEST_ISSUE_TYPE}".
   Si ya hay casos, ANTES de diseñar los nuevos avisale al usuario: cuántos casos existen, cuál es el último
   (por ejemplo "Caso de Prueba 14" o "CP-MAADM-6820-07") y con qué número van a arrancar los nuevos
   (el más alto existente + 1, contando tanto el formato viejo como el nuevo; ej. CP-MAADM-6820-15).
   Esperá a que el usuario confirme antes de seguir. No repitas lo que los casos existentes ya cubren.
   Si no hay casos, arrancá en 01.
3. Diseñá los casos cubriendo cada criterio de aceptación: camino feliz, validaciones, cancelaciones, mensajes de
   éxito y error, y casos borde que se desprendan de la historia. Un caso por comportamiento a verificar.
   No inventes reglas que la historia no diga; si algo es ambiguo, preguntalo o marcalo como supuesto.
4. Mostrale al usuario la lista completa con este formato y preguntale si quiere agregar, quitar o cambiar alguno:

<template_caso_prueba>
{TEST_TEMPLATE}
</template_caso_prueba>

5. Solo cuando el usuario apruebe, creá un ticket por caso con jira_create_issue: project_key = proyecto de la historia,
   issue_type "{TEST_ISSUE_TYPE}", summary "CP-<HU>-<NN> - Título" (ej. "CP-MAADM-6820-01 - ..."), description con el formato de arriba y
   additional_fields {{"parent": "<CLAVE DE LA HISTORIA>"}}. El sistema completa Área solicitante y Prioridad solo.
   Creá los casos de a uno, en orden.
6. Al final, listá las claves creadas con su título. Si alguno falla, explicá el error y preguntá antes de seguir.

## Reportes
Cuando pidan un reporte, armá la JQL adecuada (siempre filtrando issuetype = "{BUG_ISSUE_TYPE}"), consultá con jira_search
(pedí los campos summary, status, priority, assignee, created, updated, labels, components) y generá un
reporte en Markdown con: resumen ejecutivo, totales por estado y prioridad, bugs críticos/bloqueantes abiertos,
bugs más antiguos sin resolver, y una tabla con clave, resumen, estado, prioridad y responsable.
Incluí la JQL usada al final. Si el usuario quiere guardarlo, usá guardar_reporte.

Respondé siempre en español."""


async def guardar_reporte(nombre_archivo: str, contenido_markdown: str) -> str:
    """Guarda un reporte en Markdown en la carpeta local 'reportes'.

    Args:
        nombre_archivo: Nombre del archivo sin ruta, por ejemplo 'bugs-sprint-12.md'.
        contenido_markdown: Contenido completo del reporte en Markdown.
    """
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / Path(nombre_archivo).name
    if path.suffix.lower() != ".md":
        path = path.with_suffix(".md")
    path.write_text(contenido_markdown, encoding="utf-8")
    return f"Reporte guardado en {path}"


def _normalize(text: str) -> str:
    """Minúsculas y sin tildes, para comparar nombres de campos."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().strip().lower()


def _writable(value):
    """Convierte el valor leído de un campo de Jira al formato que acepta Jira para escribirlo."""
    if isinstance(value, list):
        return [_writable(v) for v in value]
    if isinstance(value, dict):
        if "accountId" in value:  # campo de usuario
            return {"accountId": value["accountId"]}
        if "id" in value:  # opción de lista, versión, etc.
            return {"id": value["id"]}
    return value


def _display(value) -> str:
    """Texto legible de un valor de Jira para mostrar en la confirmación."""
    if isinstance(value, list):
        return ", ".join(_display(v) for v in value)
    if isinstance(value, dict):
        return str(value.get("value") or value.get("name") or value.get("displayName") or value)
    return str(value)


class JiraRest:
    """Consultas directas a la API REST de Jira para las reglas del equipo (campos copiados y opciones)."""

    def __init__(self):
        self._field_ids: dict[str, str] | None = None
        self._create_fields: dict[tuple[str, str], list[dict]] = {}
        self._http = httpx.AsyncClient(
            base_url=os.environ["JIRA_URL"].rstrip("/"),
            auth=(os.environ["JIRA_USERNAME"], os.environ["JIRA_API_TOKEN"]),
            headers={"Accept": "application/json"},
            timeout=30,
        )

    async def _get(self, path: str, **params) -> dict | list:
        response = await self._http.get(path, params=params or None)
        response.raise_for_status()
        return response.json()

    async def field_ids(self, names: list[str]) -> dict[str, str]:
        """Devuelve {nombre: customfield_XXXXX} buscando cada campo por su nombre."""
        if self._field_ids is None:
            self._field_ids = {_normalize(f["name"]): f["id"] for f in await self._get("/rest/api/3/field")}
        missing = [n for n in names if _normalize(n) not in self._field_ids]
        if missing:
            raise RuntimeError(f"No encontré en Jira los campos: {', '.join(missing)}. Revisá el .env.")
        return {n: self._field_ids[_normalize(n)] for n in names}

    async def story_data(self, issue_key: str, names: list[str]) -> tuple[dict[str, tuple[str, object]], list[str]]:
        """Devuelve ({nombre: (field_id, valor)}, componentes) de un ticket; omite los campos vacíos."""
        ids = await self.field_ids(names)
        try:
            data = await self._get(f"/rest/api/3/issue/{issue_key}", fields=",".join(["components", *ids.values()]))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise RuntimeError(f"No encontré la historia {issue_key} en Jira.") from e
            raise
        fields = data.get("fields", {})
        values = {name: (fid, fields[fid]) for name, fid in ids.items() if fields.get(fid) not in (None, "", [])}
        components = [c["name"] for c in fields.get("components") or []]
        return values, components

    async def create_fields(self, project_key: str, issue_type: str) -> list[dict]:
        """Campos (con sus opciones válidas) de la pantalla de creación para ese proyecto y tipo de ticket."""
        cache_key = (project_key, _normalize(issue_type))
        if cache_key not in self._create_fields:
            types = await self._get(f"/rest/api/3/issue/createmeta/{project_key}/issuetypes", maxResults=200)
            types = types.get("issueTypes") or types.get("values") or []
            match = next((t for t in types if _normalize(t["name"]) == _normalize(issue_type)), None)
            if not match:
                names = ", ".join(t["name"] for t in types)
                raise RuntimeError(f"El proyecto {project_key} no tiene el tipo '{issue_type}'. Tipos disponibles: {names}")
            meta = await self._get(
                f"/rest/api/3/issue/createmeta/{project_key}/issuetypes/{match['id']}", maxResults=200
            )
            self._create_fields[cache_key] = meta.get("fields") or meta.get("values") or []
        return self._create_fields[cache_key]


async def ask_choice(label: str, options: list[dict]) -> dict:
    """Muestra las opciones numeradas por consola y devuelve la elegida."""
    print(f"\n📋 Elegí {label}:")
    for i, option in enumerate(options, 1):
        print(f"   {i}. {_display(option)}")
    while True:
        answer = (await asyncio.to_thread(input, f"Número de {label} (o 'c' para cancelar): ")).strip().lower()
        if answer in {"c", "cancelar"}:
            raise RuntimeError("El usuario canceló la operación. No se creó nada en Jira.")
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        print(f"   Opción inválida: ingresá un número del 1 al {len(options)}.")


class ConfirmingSession:
    """Envuelve la sesión MCP: aplica las reglas del equipo y pide confirmación antes de escribir en Jira."""

    def __init__(self, session: ClientSession, jira: JiraRest | None = None):
        self._session = session
        self._jira = jira
        self._story_keys: set[str] = set()  # historias de los bugs creados en esta sesión
        self._auto_confirm_parent: str | None = None  # historia cuyos casos se confirmaron con "t"

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def _copy_story_data(self, arguments: dict, extra: dict) -> None:
        # La clave de la historia sale del título: "MAADM-6820 | Título de la historia - ...".
        match = STORY_KEY_RE.match(arguments.get("summary", ""))
        if not match:
            raise RuntimeError(
                "El título del bug tiene que empezar con la clave de la historia (ej. 'MAADM-6820 | ...') "
                "para poder copiar sus campos. Corregí el título y volvé a intentar."
            )
        story_key = match.group(1)
        self._story_keys.add(story_key)
        copied, story_components = await self._jira.story_data(story_key, COPY_FIELDS)

        # Componentes: los fijos del equipo + los que tenga la historia (sin repetir).
        components = [c.strip() for c in BUG_COMPONENTS.split(",") if c.strip()]
        for name in story_components:
            if name.lower() not in {c.lower() for c in components}:
                components.append(name)
        arguments["components"] = ",".join(components)
        if story_components:
            print(f"\nℹ️  Componentes de {story_key}: {', '.join(story_components)}")
        else:
            print(f"\n⚠️  La historia {story_key} no tiene componentes; el bug queda solo con {BUG_COMPONENTS}.")

        self._copy_fields(story_key, copied, extra)

    @staticmethod
    def _copy_fields(story_key: str, copied: dict, extra: dict) -> None:
        for name in COPY_FIELDS:
            if name in copied:
                field_id, value = copied[name]
                extra[field_id] = _writable(value)
                print(f"ℹ️  {name}: '{_display(value)}' (copiado de {story_key})")
            else:
                print(f"⚠️  La historia {story_key} no tiene '{name}' cargado; el ticket queda sin ese campo.")

    async def _ask_fields(self, arguments: dict, extra: dict) -> None:
        fields = await self._jira.create_fields(arguments["project_key"], BUG_ISSUE_TYPE)
        for wanted in ASK_FIELDS:
            field = next(
                (f for f in fields if wanted in (f.get("fieldId"), f.get("key")) or _normalize(f.get("name", "")) == _normalize(wanted)),
                None,
            )
            if not field:
                raise RuntimeError(
                    f"El campo '{wanted}' no está en la pantalla de creación de {BUG_ISSUE_TYPE} "
                    f"en {arguments['project_key']}. Revisá JIRA_ASK_FIELDS."
                )
            options = field.get("allowedValues") or []
            if not options:
                raise RuntimeError(f"El campo '{field['name']}' no tiene opciones para elegir en Jira.")
            choice = await ask_choice(field["name"], options)
            is_multi = field.get("schema", {}).get("type") == "array"
            extra[field["fieldId"]] = [{"id": choice["id"]}] if is_multi else {"id": choice["id"]}
            print(f"   ✔ {field['name']}: {_display(choice)}")

    async def _apply_bug_rules(self, arguments: dict) -> dict:
        # Regla del equipo: todo bug lleva siempre estos componentes, diga lo que diga el modelo.
        arguments = {**arguments, "issue_type": BUG_ISSUE_TYPE, "components": BUG_COMPONENTS}
        if not self._jira:
            return arguments
        extra = arguments.get("additional_fields") or {}
        if isinstance(extra, str):
            extra = json.loads(extra) if extra.strip() else {}
        await self._copy_story_data(arguments, extra)
        if ASK_FIELDS:
            await self._ask_fields(arguments, extra)
        arguments["additional_fields"] = json.dumps(extra, ensure_ascii=False)
        return arguments

    async def _apply_test_rules(self, arguments: dict) -> tuple[dict, str]:
        """Caso de prueba: tipo TEST, Principal = historia, Área solicitante de la historia y prioridad fija."""
        arguments = {k: v for k, v in arguments.items() if k != "components"}
        arguments["issue_type"] = TEST_ISSUE_TYPE
        # "CA-1" Jira lo convierte en link a un ticket inexistente: lo normalizamos a "CA01".
        if arguments.get("description"):
            arguments["description"] = CA_RE.sub(lambda m: f"CA{int(m.group(1)):02d}", arguments["description"])
        extra = arguments.get("additional_fields") or {}
        if isinstance(extra, str):
            extra = json.loads(extra) if extra.strip() else {}
        parent = extra.get("parent")
        parent = parent.get("key") if isinstance(parent, dict) else parent
        if not parent:
            raise RuntimeError(
                'Los casos de prueba necesitan la historia como principal: additional_fields {"parent": "MAADM-1234"}.'
            )
        extra["parent"] = parent
        # "Historia de Usuario" siempre como link a la historia del Principal.
        if arguments.get("description"):
            story_url = f"{os.environ['JIRA_URL'].rstrip('/')}/browse/{parent}"
            arguments["description"] = HU_LINE_RE.sub(
                lambda _: f"**Historia de Usuario:** [HU-{parent}]({story_url})", arguments["description"], count=1
            )
        if self._jira:
            print(f"\n🧪 {arguments.get('summary')}")
            copied, _ = await self._jira.story_data(parent, COPY_FIELDS)
            self._copy_fields(parent, copied, extra)
            fields = await self._jira.create_fields(arguments["project_key"], TEST_ISSUE_TYPE)
            priority = next((f for f in fields if f.get("fieldId") == "priority"), None)
            option = next(
                (o for o in (priority or {}).get("allowedValues", [])
                 if _normalize(o.get("name", "")) == _normalize(TEST_PRIORITY)),
                None,
            )
            if option:
                extra["priority"] = {"id": option["id"]}
            else:
                print(f"⚠️  No encontré la prioridad '{TEST_PRIORITY}' para {TEST_ISSUE_TYPE}; queda la de Jira por defecto.")
        arguments["additional_fields"] = json.dumps(extra, ensure_ascii=False)
        return arguments, parent

    def _apply_link_rules(self, arguments: dict) -> dict:
        """El bug bloquea la historia: inward = bug, outward = historia (mismo formato que MAADM-9252)."""
        arguments = {**arguments, "link_type": LINK_TYPE}
        inward, outward = arguments.get("inward_issue_key"), arguments.get("outward_issue_key")
        if inward in self._story_keys and outward not in self._story_keys:
            arguments["inward_issue_key"], arguments["outward_issue_key"] = outward, inward  # el modelo los invirtió
        return arguments

    async def call_tool(self, name, arguments=None, *args, **kwargs):
        test_parent = None
        if name == "jira_create_issue":
            if _normalize((arguments or {}).get("issue_type") or "") == _normalize(TEST_ISSUE_TYPE):
                arguments, test_parent = await self._apply_test_rules(arguments or {})
            else:
                arguments = await self._apply_bug_rules(arguments or {})
        if name == "jira_create_issue_link":
            arguments = self._apply_link_rules(arguments or {})
            print(f"\nℹ️  Vínculo: {arguments['inward_issue_key']} {LINK_TYPE.lower()} {arguments['outward_issue_key']}")
        if name in WRITE_TOOLS:
            if test_parent and test_parent == self._auto_confirm_parent:
                print("   ✔ Confirmado (sí a todos los casos de esta historia)")
            else:
                print(f"\n⚠️  El agente quiere ejecutar {name} con:")
                for key, value in (arguments or {}).items():
                    print(f"   {key}: {value}")
                hint = "s/n, t = sí a todos los casos de esta historia" if test_parent else "s/n"
                answer = (await asyncio.to_thread(input, f"¿Confirmás? ({hint}): ")).strip().lower()
                if test_parent and answer in {"t", "todos"}:
                    self._auto_confirm_parent = test_parent
                elif answer not in {"s", "si", "sí", "y", "yes"}:
                    raise RuntimeError("El usuario canceló la operación. No se creó nada en Jira.")
        return await self._session.call_tool(name, arguments, *args, **kwargs)


def mcp_server_params() -> StdioServerParameters:
    missing = [v for v in ("JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN") if not os.getenv(v)]
    if missing:
        sys.exit(f"Faltan variables en .env: {', '.join(missing)} (ver .env.example)")
    # Preferimos el mcp-atlassian instalado en el mismo entorno; si no está, lo corremos con uvx.
    local = shutil.which("mcp-atlassian", path=str(Path(sys.executable).parent))
    command, args = (local, []) if local else ("uvx", ["mcp-atlassian"])
    return StdioServerParameters(
        command=command,
        args=args,
        env={
            **os.environ,
            "JIRA_URL": os.environ["JIRA_URL"],
            "JIRA_USERNAME": os.environ["JIRA_USERNAME"],
            "JIRA_API_TOKEN": os.environ["JIRA_API_TOKEN"],
            "ENABLED_TOOLS": ",".join(JIRA_TOOLS),
        },
    )


def tool_result_text(result) -> str:
    """Convierte el resultado de una tool MCP en texto plano."""
    text = "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")
    return text or "(sin contenido)"


class GeminiAgent:
    """Agente con Gemini: loop de function calling sobre las tools MCP."""

    def __init__(self, session: ConfirmingSession, mcp_tools: list):
        from google import genai
        from google.genai import types

        self.types = types
        self.session = session
        api_key = os.environ["GEMINI_API_KEY"]
        # Las claves "AQ." son de Vertex AI (modo express); las de AI Studio empiezan con "AIza".
        default_vertex = "true" if api_key.startswith("AQ.") else "false"
        use_vertex = os.getenv("GEMINI_USE_VERTEXAI", default_vertex).lower() == "true"
        self.client = genai.Client(api_key=api_key, vertexai=use_vertex)
        self.name = f"Gemini ({GEMINI_MODEL}{', Vertex AI' if use_vertex else ''})"

        declarations = [
            types.FunctionDeclaration(
                name=t.name, description=t.description or "", parameters_json_schema=t.inputSchema
            )
            for t in mcp_tools
        ]
        declarations.append(
            types.FunctionDeclaration(
                name="guardar_reporte",
                description="Guarda un reporte en Markdown en la carpeta local 'reportes'.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "nombre_archivo": {
                            "type": "string",
                            "description": "Nombre del archivo sin ruta, por ejemplo 'bugs-sprint-12.md'.",
                        },
                        "contenido_markdown": {
                            "type": "string",
                            "description": "Contenido completo del reporte en Markdown.",
                        },
                    },
                    "required": ["nombre_archivo", "contenido_markdown"],
                },
            )
        )
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        self.contents: list = []

    async def call(self, name: str, args: dict) -> dict:
        try:
            if name == "guardar_reporte":
                return {"result": await guardar_reporte(**args)}
            result = await self.session.call_tool(name, args)
            return {"error" if result.isError else "result": tool_result_text(result)}
        except Exception as e:  # el modelo recibe el error y puede corregir o avisar
            return {"error": str(e)}

    async def turn(self, user_input: str) -> None:
        start = len(self.contents)
        try:
            await self._turn(user_input)
        except Exception:
            del self.contents[start:]  # descartamos el turno fallido para poder reintentar
            raise

    async def _turn(self, user_input: str) -> None:
        types = self.types
        self.contents.append(types.Content(role="user", parts=[types.Part(text=user_input)]))
        while True:
            response = await self.client.aio.models.generate_content(
                model=GEMINI_MODEL, contents=self.contents, config=self.config
            )
            if not response.candidates or not response.candidates[0].content:
                print("\n(El modelo no devolvió respuesta.)")
                return
            # Guardamos el contenido original (incluye las firmas de razonamiento de Gemini).
            content = response.candidates[0].content
            self.contents.append(content)
            for part in content.parts or []:
                if part.text and part.text.strip() and not part.thought:
                    print(f"\n🤖 {part.text}")
            calls = response.function_calls
            if not calls:
                return
            parts = []
            for fc in calls:
                print(f"   ↳ usando {fc.name}...")
                result = await self.call(fc.name, dict(fc.args or {}))
                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(id=fc.id, name=fc.name, response=result)
                    )
                )
            self.contents.append(types.Content(role="user", parts=parts))


class ClaudeAgent:
    """Agente con Claude: el tool runner del SDK de Anthropic maneja el loop."""

    def __init__(self, session: ConfirmingSession, mcp_tools: list):
        from anthropic import AsyncAnthropic, beta_async_tool
        from anthropic.lib.tools.mcp import async_mcp_tool

        self.client = AsyncAnthropic()  # usa ANTHROPIC_API_KEY
        self.tools = [async_mcp_tool(t, session) for t in mcp_tools]
        self.tools.append(beta_async_tool(guardar_reporte))
        self.messages: list = []
        self.name = f"Claude ({CLAUDE_MODEL})"

    async def turn(self, user_input: str) -> None:
        start = len(self.messages)
        try:
            await self._turn(user_input)
        except Exception:
            del self.messages[start:]  # descartamos el turno fallido para poder reintentar
            raise

    async def _turn(self, user_input: str) -> None:
        self.messages.append({"role": "user", "content": user_input})
        runner = self.client.beta.messages.tool_runner(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            tools=self.tools,
            messages=self.messages,
        )
        async for message in runner:
            # El runner tiene su propia copia del historial; lo espejamos para el próximo turno.
            self.messages.append({"role": "assistant", "content": message.content})
            for block in message.content:
                if block.type == "text" and block.text.strip():
                    print(f"\n🤖 {block.text}")
                elif block.type == "tool_use":
                    print(f"   ↳ usando {block.name}...")
            tool_response = await runner.generate_tool_call_response()
            if tool_response is not None:
                self.messages.append(tool_response)
            if message.stop_reason == "refusal":
                print("\n(El modelo rechazó la solicitud.)")


def choose_provider() -> str:
    """LLM_PROVIDER manda; si no está, usa Gemini si hay clave y si no Claude."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not provider:
        provider = "gemini" if os.getenv("GEMINI_API_KEY") else "claude"
    if provider not in {"gemini", "claude"}:
        sys.exit("LLM_PROVIDER tiene que ser 'gemini' o 'claude'")
    if provider == "gemini" and not os.getenv("GEMINI_API_KEY"):
        sys.exit("Falta GEMINI_API_KEY en .env")
    if provider == "claude" and not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("Falta GEMINI_API_KEY (o ANTHROPIC_API_KEY si querés usar Claude) en .env")
    return provider


async def main() -> None:
    provider = choose_provider()

    async with stdio_client(mcp_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = [t for t in (await session.list_tools()).tools if t.name in JIRA_TOOLS]
            confirming = ConfirmingSession(session, JiraRest())
            agent_cls = GeminiAgent if provider == "gemini" else ClaudeAgent
            agent = agent_cls(confirming, mcp_tools)

            print(f"Conectado a {os.environ['JIRA_URL']} con {len(mcp_tools)} herramientas de Jira.")
            print(f"Modelo: {agent.name}")
            print("Describí un bug para crearlo o pedí un reporte. Escribí 'salir' para terminar.\n")

            while True:
                user_input = (await asyncio.to_thread(input, "\n👤 Vos: ")).strip()
                if user_input.lower() in {"salir", "exit", "quit"}:
                    break
                if not user_input:
                    continue
                try:
                    await agent.turn(user_input)
                except Exception as e:
                    print(f"\n❌ Error de {agent.name}: {getattr(e, 'message', None) or e}")
                    print("   Podés corregir la configuración y volver a intentar, o escribir 'salir'.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # emojis en la consola de Windows
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
