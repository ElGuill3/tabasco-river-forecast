# Notas de trazabilidad y ambigüedades del borrador T10

Estas notas no modifican el Log ni los resultados. Documentan las decisiones editoriales necesarias para leerlos juntos.

| Punto de la fuente | Tratamiento en el informe |
|---|---|
| L70 fija autores e institución, pero no proporciona una cadena literal para el título | Se usa un título descriptivo del estudio; los autores y la institución se transcriben y la fecha es la solicitada. No se atribuye ese título a una cita literal de L70 |
| L28 y otras entradas iniciales mencionan 16 estaciones; L41 corrige a 14 estaciones en 13 ríos | Se adopta el inventario corregido y se indica que hubo una corrección |
| L4 aproxima la cuenca en 49 735 km²; L9 registra el trazado versionado de 51 457 km² | Se diferencia la aproximación inicial del trazado finalmente utilizado |
| L70 resume Oxolotán entre los no aceptados, pero L66 y el experimento 16 aceptan h7 | Se informa rechazo en h1/h3 y aceptación en h7; no publicación por L67. No se borra la excepción favorable |
| L27 informa AUC h1 de 0.981; 09-independent.md informa 0.997 | El informe cita el resultado que el lector puede abrir, sin usar el AUC para justificar publicación; la discrepancia histórica queda registrada aquí. No se vuelve a puntuar |
| L27 dice que R2 avisa desde el 9 de octubre; la tabla diaria de 09-independent.md marca False el 9 y 10, True desde el 11 | El informe sigue la tabla diaria y remite a estas notas para el conflicto histórico, sin modificar las fuentes |
| L26 propone validez hasta el 13 de octubre; el resultado 09 y L27 incluyen también el 14 | La figura usa las fechas realmente presentes en el resultado, incluido el 14; no afirma que todo el catálogo termine el 13 |
| 09-independent.md conserva la petición de SIH nuevo como pendiente | Se aplica la información posterior de L28: el SIH de BDCTB sigue sin datos después del 3 de septiembre de 2025 |
| Etiquetas M1 en la parte A de 12/13 | Se aclara que el comparador de esa parte es el original ajustado con desarrollo, no el ajuste de despliegue hasta 2022; L40 ya lo advierte |
| L35 no explicita cómo entrenar L1 cuando la serie IPCET real empieza tarde | Se conserva la interpretación previa a puntuar de L36: disponibilidad simulada en entrenamiento y serie real al evaluar |
| La equivalencia SIH diario frente a IPCET 06:00 es una hipótesis, no una identidad confirmada | Se informa concordancia empírica y se mantiene la diferencia como amenaza a la validez |
| L61 exige habilidad también para respaldo; L65 decide aceptación con L1 y L67 sirve respaldo M1 | Se distingue aceptación L1 y continuidad del producto; la pérdida de M1 frente a persistencia se cuantifica y no se declara cumplimiento de la condición original para respaldo |
| Los modelos nuevos se puntúan con edad cero; la operación conserva edades y respaldo de BDCTB | Se identifica como transferencia operativa no validada con el experimento 16 |
| L39 corrige un desfase no encontrado en el diagnóstico 07 | Se presenta el diagnóstico como resultado histórico, no como evidencia definitiva de ausencia de defectos de alineación |
| Las coordenadas disponibles en el resultado 16 son etiquetas de celda CEMS, con desplazamiento respecto de Open-Meteo y varias estaciones por celda | El mapa dice explícitamente que son celdas representativas, no coordenadas de aforo. Agrupa etiquetas sin desplazar puntos para aparentar ubicaciones distintas |
| L71 añade la verificación prospectiva acumulada hasta el 23 de octubre de 2026 | Sustituye la afirmación anterior de ausencia de agregado: h1 tiene 4 evaluados, MAE 0.47 m frente a 0.69 m de persistencia, sesgo −0.10 m y cobertura 50 %; h3 tiene 2 evaluados, MAE 0.64 m frente a 2.45 m, sesgo −0.64 m y cobertura 100 %; h7 sigue sin pares. Se incluyen pendientes y casos del 22/23, sin inferir estabilidad |
| Las publicaciones pueden sobrescribir la historia de una fecha | Se advierte que esa historia no conserva necesariamente todas las versiones vistas por usuarios |
| Portales SIH y datos CONAGUA con bucle de redirecciones; página IPCET concreta con 404 | Se excluyen esas URL de las referencias verificadas. El portal Tabasco que sí responde se identifica como referencia institucional, no como validación de cada tabla |

## Alcance del entregable

- Markdown, figuras y PDF. El PDF se genera desde `informe.md` con `docs/report/latex/build.sh` (pandoc + tectonic); no se edita a mano.
- S4 es la especificación principal de T10. El contenido documenta además la evidencia de S1, S3, S5, S6, S7 y S8; no completa la cobertura espacial pretendida en S3 ni implementa tareas de operación nuevas.
- No se reentrenaron modelos, no se regeneraron puntuaciones, no se modificaron datos, código de producción, pruebas, resultados ni ODD.
- Figuras generadas con Pillow ya disponible. La fuente tipográfica DejaVu Sans debe estar accesible al sistema para regenerar la misma presentación; no se instalaron paquetes ni fuentes.
- La comprobación `.cache/t10/check_report.py` verifica estructura, enlaces locales, caracteres prohibidos, ausencia de identificadores internos en el informe, transcripción de las filas por estación de los experimentos 15/16, tabla prospectiva y casos diarios contra L71, hashes, DPI y repetibilidad con conexiones de red bloqueadas.
- El informe ya no requiere conocer los identificadores del Log: los sujetos y citas se reescribieron por experimento, archivo o sección. La historia completa se enlaza una sola vez en Reproducibilidad; estas notas conservan los identificadores únicamente para trazabilidad editorial.
- El nombre interno del modelo con nivel también se expande en texto y figuras para que el grep de identificadores no lo confunda con una referencia al Log. M1 queda definido como modelo sin nivel vivo.
- Las reglas de aceptación y publicación se enuncian completas una sola vez, en la sección 4.4. Las secciones posteriores remiten a ella; se mantiene la advertencia sobre el respaldo que no supera persistencia.
- La tabla de la sección 5.13 se transcribe de L71 y se contrasta automáticamente con esa entrada. No se ejecutó otra puntuación con archivos actuales de la rama de datos. El comando de reproducción requiere conservar la instantánea hasta el 23 de octubre: nuevas observaciones o revisiones pueden cambiar el resultado.
- Los casos nuevos muestran 19.17 m en PREVENCION el 22 de octubre, fuera de la banda a un día; y 19.23 m en ALERTA el 23, dentro de banda y 2 cm sobre el NAMO. Se indica expresamente que n=4 no permite conclusiones.
- La primera comprobación de DPI fue demasiado estricta: PNG guarda píxeles por metro enteros y 150 dpi vuelve como 150.0124. Se corrigió la tolerancia del comprobador, no la resolución de las figuras.
- El primer lint informó dos cadenas largas; se dividieron y el control final pasó. Ninguno de esos fallos constituye evidencia RED de una nueva regla del modelo.
