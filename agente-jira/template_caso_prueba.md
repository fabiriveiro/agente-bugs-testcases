Título del ticket (campo "summary"): CP-<HU>-<NN> - <Título del caso>
- <HU> es la clave completa de la historia (para la historia MAADM-6820 es MAADM-6820).
- <NN> es el número de caso con dos dígitos: 01, 02, ... 10, 11.
Ejemplo: "CP-MAADM-6820-01 - Visualización de modal en modo Alta"

La descripción del ticket va en Markdown con exactamente este formato (mismos títulos, niveles y orden):

### CP-<HU>-<NN> - <Título del caso>

**Historia de Usuario:** [HU-<HU>](<URL de Jira>/browse/<HU>)
**Criterio de aceptación asociado:** CA<NN>
**Referencia Figma:** <link o nombre de frame>

#### Qué se prueba
<descripción breve del comportamiento a validar>

#### Precondiciones
- <precondición 1>
- <precondición 2>

#### Datos de prueba
- <dato/usuario/input 1>
- <dato/usuario/input 2>

#### Pasos
1. <paso 1>
2. <paso 2>
3. <paso 3>

#### Resultado esperado
- <resultado verificable 1>
- <resultado verificable 2>

Reglas para completarlo:
- Historia de Usuario: va como link a la historia, por ejemplo
  [HU-MAADM-6869](https://fpatronal.atlassian.net/browse/MAADM-6869). El sistema lo arma solo al crear el ticket.
- Criterio de aceptación asociado: el CA de la historia que cubre el caso, siempre con dos dígitos y sin guion (CA01, CA02...), como los numera la historia. Nunca uses "CA-1": Jira lo convierte en un link a un ticket inexistente. Si cubre varios, listalos
  separados por coma. Si la historia no tiene criterios numerados, numeralos en el orden en que aparecen y aclaralo
  al usuario.
- Referencia Figma: buscá links de Figma (figma.com) o nombres de frames en la historia. Si no hay, preguntale al
  usuario una sola vez para todos los casos; si no tiene, poné "N/A".
- Datos de prueba: usá los que estén en la historia o te dé el usuario (usuarios, pólizas, contratos, inputs).
  Si no hay, describí el tipo de dato necesario (ej. "Contrato sin plan de pago existente") y avisale al usuario
  que conviene completarlos con datos reales.
- Pasos: uno por línea, en infinitivo ("Acceder a...", "Completar...", "Presionar...").
- Resultado esperado: cada punto tiene que ser verificable (textos exactos de títulos, botones y mensajes cuando la
  historia los indique).
