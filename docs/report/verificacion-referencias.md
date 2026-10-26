# Verificación de las referencias del borrador

Se verificó la resolución mediante cabeceras HTTP, con el único acceso de red autorizado: `timeout 20 curl -sIL <url>`. No se descargaron datos ni se usó la resolución del enlace como validación del desempeño del modelo. Los resultados siguientes son observados en esta sesión; no afirman disponibilidad permanente.

## Referencias incluidas

| Referencia en informe.md | URL comprobada | Resultado observado |
|---|---|---|
| [1] Alfieri et al., GloFAS | `https://doi.org/10.5194/hess-17-1161-2013` | HTTP 302 al editor, luego HTTP 200; salida 0 |
| [2] Harrigan et al., GloFAS-ERA5 | `https://doi.org/10.5194/essd-12-2043-2020` | HTTP 302 al editor, luego HTTP 200; salida 0 |
| [3] Romano et al., Conformalized Quantile Regression | `https://proceedings.neurips.cc/paper/2019/hash/5103c3584b063c431bd1268e9b5e76fb-Abstract.html` | HTTP 200; salida 0 |
| [4] Bartholmes et al., EFAS | `https://doi.org/10.5194/hess-13-141-2009` | HTTP 302 al editor, luego HTTP 200; salida 0 |
| [5] CEMS, catálogo operativo | `https://ewds.climate.copernicus.eu/datasets/cems-glofas-forecast?tab=overview` | HTTP 200; salida 0 |
| [6] Open-Meteo, Flood API | `https://open-meteo.com/en/docs/flood-api` | HTTP 200; salida 0 |
| [7] Gobierno de Tabasco, portal institucional | `https://tabasco.gob.mx/` | HTTP 200; salida 0. No certifica cada página histórica IPCET |
| [8] NASA, IMERG | `https://gpm.nasa.gov/data/imerg` | HTTP 200; salida 0 |

## Candidatos descartados y limitaciones

- `https://doi.org/10.5194/hess-24-1075-2020`: HTTP 404. No se incluyó ni se le atribuyó una obra.
- `https://doi.org/10.1002/hyp.697`: HTTP 404. No se incluyó ni se le atribuyó una obra.
- `https://sih.conagua.gob.mx/`: bucle HTTP 307; curl terminó con salida 47. No se incluyó como referencia verificada. El método sigue identificando la procedencia SIH de los archivos locales por el Log.
- `https://datos.conagua.gob.mx/`: bucle HTTP 307; curl terminó con salida 47. No se incluyó como referencia verificada.
- `https://tabasco.gob.mx/noticias/semaforo-hidrometeorologico-y-niveles-de-rios-20102026`: HTTP 404. Se descartó la página concreta como referencia. Se incluye el portal oficial que sí resolvió, acotando el alcance de esa comprobación.
- Se intentó una consulta de un DOI candidato de verificación económica sin entrecomillar sus caracteres especiales; el shell la rechazó con salida 2 antes de ejecutar curl. No hubo petición HTTP ni referencia incluida por ese intento. El informe no depende de esa cita.

La comprobación de cabeceras constata resolución, no cotejo bibliográfico íntegro de contenido. Solo se listaron obras y servicios identificables; los resultados locales se citan por archivo y entrada L, no como hallazgos de esas publicaciones.
