# Pronóstico para Radar Mendoza

Cada media hora, una Action busca la última corrida de dos modelos. Cuando sale una corrida nueva, recorta el cuadro
del radar de Mendoza (de unos 500 × 700 km), lo pasa a Mercator con 250 × 348 píxeles (2 km cada uno) y publica en
GitHub Pages un PNG gris de 8 bits por capa y hora, hasta 72 h:

- **Pronóstico del SMN (modelo WRF)**, del Servicio Meteorológico Nacional: lluvia en la hora, temperatura, viento y
  rumbo. Datos: <https://registry.opendata.aws/smn-ar-wrf-dataset/>
- **DWD (ICON)**, del Deutscher Wetterdienst: nubes (% del cielo cubierto). Datos: <https://opendata.dwd.de/weather/nwp/icon/>,
  licencia CC BY 4.0.

Son pronósticos de modelos, tal como salen de cada fuente, sin cálculos propios encima.

`meta.json` trae las corridas, las horas (UTC, `AAAAMMDDHH`), la caja y cómo pasar cada byte a su unidad:
`valor = desde + paso × byte`. Archivos: `wrf/{hora}-{lluvia|temp|viento|rumbo}.png` e `icon/{hora}-nubes.png`.

**Acierto:** en cada vuelta, `python pronostico.py medir datos/acierto.csv` suma a la planilla `acierto.csv` (rama
`datos`) cada hora terminada: el alcance del radar (250 km) en cajas de 20 km, y en cuántas vieron lluvia los dos (el
WRF de la última corrida publicada una hora antes, 0,5 mm o más, y el radar ya limpio de ruido, «Débil» o más), solo el
pronóstico o solo el radar. Hora en UTC, la de su comienzo. Solo mide: no corrige el modelo.

Probar en la compu: `pip install -r requirements.txt` y
`python pronostico.py armar sitio --wrf AAAAMMDDHH --icon AAAAMMDDHH --plazos 0,1,24`.
