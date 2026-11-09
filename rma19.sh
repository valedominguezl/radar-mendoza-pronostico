#!/usr/bin/env bash
# Guarda en la carpeta dada las imágenes del radar del SMN en Santa Rosa (RMA19, SINARAME) que estén en su caché y
# falten: `AAAAMMDD/hhmm.png`, con la hora de Mendoza, como las nombra el SMN. Sirve para medir 2 semanas cuántas da y
# cuánto acuerdan con Contingencias. El caché guarda ~9 h y GitHub saltea muchas corridas programadas (a veces pasan
# 7 h entre dos): cada vuelta revisa las últimas 10 h, así una corrida tardía recupera lo que faltó.
# `vueltas.csv` anota qué tramo revisó cada vuelta: una hora sin imagen dentro de un tramo revisado es una que el SMN no
# dio; fuera de todos, una que no se llegó a mirar. Con curl y no Python: el certificado de su servidor falla con el
# almacén de Python de algunas máquinas.
set -euo pipefail

dir=${1:?carpeta}
base=https://radares.hidricosargentina.gob.ar/cache/RMA19
# Mendoza está en UTC−3 todo el año (en TZ de POSIX el signo va al revés).
mza='<-03>3'

ahora=$(date -u +%s)
# Una imagen aparece ~11 min después de su hora; las que tarden más se buscan otra vez en la vuelta siguiente.
hasta=$(( (ahora - 10 * 60) / 600 * 600 ))
desde=$(( hasta - 10 * 3600 ))
nuevas=0
for (( t = desde; t <= hasta; t += 600 )); do
  nombre=$(TZ=$mza date -d "@$t" +%Y%m%d%H%M%S)
  archivo="$dir/${nombre:0:8}/${nombre:8:4}.png"
  [ -f "$archivo" ] && continue
  mkdir -p "$dir/${nombre:0:8}"
  # Solo un PNG: si falta, el servidor contesta una página de error.
  if curl -sf --max-time 30 -A "radar-mendoza/0.1 (prototipo de uso personal, sin fines comerciales)" \
       -o "$archivo.tmp" "$base/$nombre.png" \
     && [ "$(head -c 4 "$archivo.tmp" | od -An -tx1 | tr -d ' \n')" = "89504e47" ]; then
    mv "$archivo.tmp" "$archivo"
    nuevas=$(( nuevas + 1 ))
  else
    rm -f "$archivo.tmp"
  fi
done
rmdir "$dir"/*/ 2>/dev/null || true

[ -f "$dir/vueltas.csv" ] || echo "vuelta_utc,desde_mza,hasta_mza,nuevas" > "$dir/vueltas.csv"
echo "$(date -u -d "@$ahora" +%Y-%m-%dT%H:%MZ),$(TZ=$mza date -d "@$desde" +%Y%m%d%H%M),$(TZ=$mza date -d "@$hasta" +%Y%m%d%H%M),$nuevas" \
  >> "$dir/vueltas.csv"
echo "$nuevas nuevas"
