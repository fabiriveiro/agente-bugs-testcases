Título del ticket (campo "summary", no va en la descripción). Todos los bugs usan este formato exacto:

<CLAVE DE LA HISTORIA> | <Título de la historia, copiado exacto de Jira> - <Breve descripción del error>

Ejemplo real: la historia MAADM-6820 se llama "Generación planes de pago -Modal de Alta/edición de plan de pago - Gestionar creación y edición",
entonces el bug se titula
"MAADM-6820 | Generación planes de pago -Modal de Alta/edición de plan de pago - Gestionar creación y edición - Desaparece la barra de desplazamiento del modal al cancelar la cancelación del alta".

- La breve descripción del error va al final, después de " - ": una sola frase corta (idealmente menos de 12 palabras)
  que diga qué falla, empezando con mayúscula y sin punto final.

- La historia es el ticket de Jira donde se encontró el bug. El usuario puede darte la clave (MAADM-6820)
  o el link (https://fpatronal.atlassian.net/browse/MAADM-6820); en ese caso la clave es la última parte del link.
- Si el usuario no indica la historia, PREGUNTÁSELA antes de armar el título. Nunca la inventes ni la adivines.
- Consultá la historia con jira_get_issue y copiá su título (summary) EXACTO, sin corregirlo, resumirlo ni reformatearlo.
  Si no la encontrás, avisale al usuario y pedile que verifique la clave.
- El bug se crea en el mismo proyecto que la historia (la parte antes del guion, por ejemplo MAADM), salvo que el usuario diga otro.
- Mostrale el título al usuario junto con el borrador para que lo confirme.

La descripción del ticket tiene exactamente estas secciones, con estos títulos y en este orden:

**1. Descripción**
Una o dos oraciones que expliquen qué pasa y en qué pantalla o acción.

**2. Datos de prueba**
Usuario, número de póliza, plan u otros datos usados para reproducir el error (uno por línea).
Si el usuario no los da, preguntalos; si no aplican, escribir "N/A".

**3. Pasos para reproducir**
Lista con viñetas, un paso por línea, en imperativo ("Ingresar a...", "Presionar en...").
Si hay una URL, incluirla como link en el primer paso.

**4. Resultado esperado**
Una oración con lo que debería pasar.

**5. Resultado obtenido**
Una oración con lo que pasa realmente.

**6. Evidencia**
Capturas, videos o logs que mencione el usuario. Si no hay, dejar "Se adjunta evidencia." para que la suba después.

---

Ejemplo de un bug bien escrito (solo como referencia de estilo y nivel de detalle; NO reutilizar sus datos):

**1. Descripción**
Al cancelar la cancelación del alta del plan de pago desaparece la barra de desplazamiento lateral del modal.

**2. Datos de prueba**
rrosas
6066807

**3. Pasos para reproducir**
* Ingresar a la URL [MARS - Home](https://intra-test.fedpat.com.ar/mars/#/art/produccion/poliza/generacion-planes-pago/6066807)
* Presionar en alta
* Cancelar el alta
* Presionar cancelar en el modal

**4. Resultado esperado**
Se muestra el modal correctamente.

**5. Resultado obtenido**
Desaparece la barra de desplazamiento lateral.

**6. Evidencia**
Se adjunta evidencia.
