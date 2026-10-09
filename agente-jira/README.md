# Agente de bugs para Jira

Agente de consola que usa Gemini (o Claude, opcional) + el servidor MCP `mcp-atlassian` (autenticado con tu API token) para:

- **Crear bugs**: le describís el problema y arma el ticket (resumen, pasos, esperado/actual, entorno, prioridad), busca duplicados y lo crea **solo después de que confirmes**.
- **Armar casos de prueba**: le pasás el link o la clave de una historia, lee sus criterios de aceptación, revisa los casos que ya existen y propone los nuevos con el formato de `template_caso_prueba.md`. Al aprobarlos crea un ticket TEST por caso con Principal = la historia.
- **Generar reportes**: "reporte de bugs abiertos del proyecto ABC del último mes" → consulta Jira por JQL y arma un informe en Markdown que puede guardar en `reportes/`.

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Completá `.env` con tu `GEMINI_API_KEY`, `JIRA_URL`, `JIRA_USERNAME` (tu email) y `JIRA_API_TOKEN`.

- Claves de Gemini: sirven las de Google AI Studio (`AIza...`) y las de Vertex AI modo express (`AQ....`); se detecta sola.
- Para usar Claude: completá `ANTHROPIC_API_KEY` y poné `LLM_PROVIDER=claude`.
- El template de los bugs está en `template_bug.md`.

## Uso

```bash
python agente_jira.py
```

Ejemplos:
- `En el checkout, al aplicar un cupón vencido la página queda cargando para siempre. Chrome 129, ambiente QA. Proyecto SHOP.`
- `Haceme un reporte de los bugs críticos y altos abiertos en SHOP creados este mes y guardalo.`

## Seguridad

- El servidor MCP arranca solo con estas herramientas: buscar, leer issues, listar proyectos/campos y crear issue (`JIRA_TOOLS` en `agente_jira.py`). No puede borrar, editar ni transicionar tickets.
- Crear un ticket siempre pide `s/n` en la consola.
- No subas `.env` a git (ya está en `.gitignore`).
