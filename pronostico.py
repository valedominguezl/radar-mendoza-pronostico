# Pronóstico para Radar Mendoza: baja la última corrida del WRF del SMN (lluvia) y del ICON del
# DWD (nubes), recorta el cuadro del radar, lo pasa a Mercator y guarda un PNG gris de 8 bits por capa y hora, más
# meta.json con las corridas, las horas y cómo pasar cada byte a su unidad (valor = desde + paso × byte).
#
#   python pronostico.py ver URL_DEL_META_PUBLICADO [--forzar]   → renglones clave=valor para GITHUB_OUTPUT
#   python pronostico.py armar SALIDA --wrf AAAAMMDDHH --icon AAAAMMDDHH [--plazos 0,1,24]
#   python pronostico.py medir acierto.csv   → suma a la planilla las horas terminadas que falten (ver «medir»)
#   python pronostico.py fijos CARPETA_RMA19   → rehace rma19-fijos.png con el registro de la rama rma19 (ver «fijos»)
#
# «ver» usa solo la biblioteca estándar (corre antes de instalar nada): si las corridas son las ya publicadas, no se arma.
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WRF = "https://smn-ar-wrf.s3.amazonaws.com/DATA/WRF/DET"
ICON = "https://opendata.dwd.de/weather/nwp/icon/grib"
PLAZOS = range(0, 73)  # hora por hora, hasta 72 h
VIEJO_H = 18  # una corrida con más horas que esto ya no se muestra (veredicto del council)

# El cuadro del radar, como src/radar/cuadros.ts, y su caja en Mercator.
LAT, LON, KLAT, KLON, AJ = -34, -68, 110.924, 92.425, 0.046
SUR, OESTE = LAT - 250 / KLAT + AJ, LON - 250 / KLON
NORTE, ESTE = LAT + 450 / KLAT + AJ, LON + 250 / KLON
R = 6378137
ANCHO = 250  # 2 km por píxel: cada celda de 4 km del WRF queda en 2×2 sin perder nada.


def my(lat):
    return R * math.log(math.tan(math.pi / 4 + lat * math.pi / 360))


ALTO = round(ANCHO * (my(NORTE) - my(SUR)) / (math.radians(ESTE - OESTE) * R))

# Cómo leer cada byte: valor = desde + paso × byte. 255 es el tope (lo que pasa, queda en 255).
CAPAS = {
    "lluvia": {"desde": 0, "paso": 0.1, "unidad": "mm en la hora"},
    "nubes": {"desde": 0, "paso": 1, "unidad": "% del cielo cubierto"},
}


def pedir(url, tiempo=60, intentos=3, cabeza=False):
    for n in range(intentos):
        try:
            req = urllib.request.Request(url, method="HEAD" if cabeza else "GET",
                                         headers={"User-Agent": "radar-mendoza-pronostico"})
            with urllib.request.urlopen(req, timeout=tiempo) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if n == intentos - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if n == intentos - 1:
                raise
        time.sleep(5 * (n + 1))


def nombre(t):
    return t.strftime("%Y%m%d%H")


def de_nombre(s):
    return dt.datetime.strptime(s, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)


def url_wrf(corrida, plazo):
    c = corrida
    return f"{WRF}/{c:%Y/%m/%d/%H}/WRFDETAR_01H_{c:%Y%m%d_%H}_{plazo:03d}.nc"


def url_icon(corrida, cosa):
    # cosa: un plazo (int) para la cobertura total, o "CLAT"/"CLON" para las coordenadas de la grilla.
    if isinstance(cosa, int):
        return f"{ICON}/{corrida:%H}/clct/icon_global_icosahedral_single-level_{nombre(corrida)}_{cosa:03d}_CLCT.grib2.bz2"
    return f"{ICON}/{corrida:%H}/{cosa.lower()}/icon_global_icosahedral_time-invariant_{nombre(corrida)}_{cosa}.grib2.bz2"


def ultima(completa, ahora):
    # La última corrida (00, 06, 12, 18 UTC) que ya tiene la hora 72 subida; mira hasta un día atrás.
    base = ahora.replace(hour=ahora.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for k in range(5):
        c = base - dt.timedelta(hours=6 * k)
        if pedir(completa(c), tiempo=20, cabeza=True) is not None:
            return c
    return None


def ver(url_meta, forzar):
    ahora = dt.datetime.now(dt.timezone.utc)
    try:
        crudo = pedir(url_meta, tiempo=20)
        antes = json.loads(crudo) if crudo else {}
    except (ValueError, OSError):
        antes = {}
    ya = ((antes.get("wrf") or {}).get("corrida"), (antes.get("icon") or {}).get("corrida"))
    fallo = []

    # Un modelo que no contesta (el S3 del SMN o el DWD caídos) no tira la vuelta: de ese queda la corrida ya
    # publicada y el otro sigue solo (antes, el ICON caído frenaba también el WRF nuevo).
    def buscar(fuente, completa, publicada):
        try:
            c = ultima(completa, ahora)
            return c and nombre(c)
        except Exception as e:
            fallo.append(fuente)
            print(f"::warning::{fuente} no contestó ({e!r}): queda la corrida publicada", file=sys.stderr)
            return publicada

    nuevas = (buscar("WRF", lambda c: url_wrf(c, PLAZOS[-1]), ya[0]), buscar("ICON", lambda c: url_icon(c, PLAZOS[-1]), ya[1]))
    hay = any(nuevas)
    # Sin lo publicado no se sabe qué corrida dejar del que no contestó: esta vuelta no cambia nada.
    cambio = hay and (forzar or nuevas != ya) and not (fallo and not antes)
    wrf = nuevas[0] and de_nombre(nuevas[0])
    viejo = not wrf or (ahora - wrf).total_seconds() > VIEJO_H * 3600
    print(f"wrf={nuevas[0] or ''}")
    print(f"icon={nuevas[1] or ''}")
    print(f"cambio={int(bool(cambio))}")
    print(f"viejo={int(viejo)}")
    print(f"publicado: wrf {ya[0]}, icon {ya[1]}; últimas: wrf {nuevas[0]}, icon {nuevas[1]}", file=sys.stderr)


# --- armar (necesita requirements.txt) ---

def _grilla_mercator():
    import numpy as np

    x0, x1 = R * math.radians(OESTE), R * math.radians(ESTE)
    y0, y1 = my(SUR), my(NORTE)
    xs = x0 + (np.arange(ANCHO) + 0.5) * (x1 - x0) / ANCHO
    ys = y1 - (np.arange(ALTO) + 0.5) * (y1 - y0) / ALTO
    return np.meshgrid(xs, ys)


_LCC = None


class Incompleto(Exception):
    """El SMN publicó el archivo de un plazo sin alguna variable (lo sube de a partes): la vuelta siguiente lo encuentra."""


def leer_wrf(args):
    # Un plazo del WRF: lee por rango solo la lluvia acumulada (un bloque zlib del campo entero) y devuelve el
    # recorte en Mercator, vecino más cercano. La página no usa temperatura ni viento; pedirlos hacía caer la corrida
    # entera cuando el SMN publicaba un plazo sin alguna de esas variables.
    corrida, plazo = args
    global _LCC
    import fsspec
    import h5py
    import numpy as np
    from pyproj import Transformer

    if _LCC is None:
        lcc = "+proj=lcc +lat_1=-35 +lat_2=-35 +lat_0=-34.99999 +lon_0=-65 +a=6370000 +b=6370000 +units=m +no_defs"
        MX, MY = _grilla_mercator()
        _LCC = Transformer.from_crs("EPSG:3857", lcc, always_xy=True).transform(MX, MY)
    LX, LY = _LCC
    fs = fsspec.filesystem("http", block_size=2**20, cache_type="none")
    for n in range(3):
        try:
            with fs.open(url_wrf(corrida, plazo), "rb", block_size=2**20, cache_type="bytes") as f, h5py.File(f, "r") as h:
                x, y = h["x"][:], h["y"][:]
                ix = np.rint((LX - x[0]) / (x[1] - x[0])).astype(int)
                iy = np.rint((LY - y[0]) / (y[1] - y[0])).astype(int)
                if "PP" not in h:
                    raise Incompleto(f"plazo {plazo} sin PP")
                return plazo, {"PP": h["PP"][0][iy, ix].astype("float32")}
        except Exception as e:
            if n == 2:
                # Un plazo que todavía no está (404) es el SMN subiendo la corrida, como el que llega sin PP.
                if isinstance(e, FileNotFoundError):
                    raise Incompleto(f"plazo {plazo} todavía no está") from e
                raise
            time.sleep(10 * (n + 1))


def _grib(datos):
    import bz2

    import eccodes

    g = eccodes.codes_new_from_message(bz2.decompress(datos))
    try:
        return eccodes.codes_get_values(g)
    finally:
        eccodes.codes_release(g)


_INDICES = None


def _icon_inicio(indices):
    global _INDICES
    _INDICES = indices


def leer_icon(args):
    corrida, plazo = args
    datos = pedir(url_icon(corrida, plazo), tiempo=120)
    if datos is None:
        raise RuntimeError(f"falta ICON {nombre(corrida)} +{plazo}")
    return plazo, _grib(datos)[_INDICES].astype("float32")


def indices_icon(corrida):
    # Para cada píxel del cuadro, la celda de la grilla de triángulos del ICON más cercana.
    import numpy as np
    from scipy.spatial import cKDTree

    lat = _grib(pedir(url_icon(corrida, "CLAT"), tiempo=120))
    lon = _grib(pedir(url_icon(corrida, "CLON"), tiempo=120))
    cerca = np.flatnonzero((lat > SUR - 1) & (lat < NORTE + 1) & (lon > OESTE - 1) & (lon < ESTE + 1))
    k = math.cos(math.radians(LAT))
    arbol = cKDTree(np.c_[lat[cerca], lon[cerca] * k])
    MX, MY = _grilla_mercator()
    plat = np.degrees(2 * np.arctan(np.exp(MY / R)) - np.pi / 2)
    plon = np.degrees(MX / R)
    _, i = arbol.query(np.c_[plat.ravel(), plon.ravel() * k])
    return cerca[i].reshape(ALTO, ANCHO)


def guardar(carpeta, t, capa, valor):
    import numpy as np
    from PIL import Image

    c = CAPAS[capa]
    b = np.clip(np.rint((valor - c["desde"]) / c["paso"]), 0, 255).astype(np.uint8)
    Image.fromarray(b).save(carpeta / f"{nombre(t)}-{capa}.png", optimize=True)


def copiar_icon(url_meta, sal):
    # Las nubes de la corrida ya publicada, bajadas tal cual del sitio (meta y PNG). None si no se pudo.
    if not url_meta:
        return None
    try:
        icon = json.loads(pedir(url_meta, tiempo=20) or b"{}").get("icon")
        if not icon or not icon.get("horas"):
            return None
        base = url_meta.rsplit("/", 1)[0]
        for h in icon["horas"]:
            datos = pedir(f"{base}/icon/{h}-nubes.png", tiempo=20)
            if not datos:
                return None
            (sal / "icon" / f"{h}-nubes.png").write_bytes(datos)
        return icon
    except (ValueError, OSError):
        return None


def armar(salida, wrf, icon, plazos, antes=None):
    from concurrent.futures import ProcessPoolExecutor
    from pathlib import Path

    import numpy as np

    sal = Path(salida)
    (sal / "wrf").mkdir(parents=True, exist_ok=True)
    (sal / "icon").mkdir(parents=True, exist_ok=True)
    meta = {
        "generado": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "caja": {"sur": SUR, "oeste": OESTE, "norte": NORTE, "este": ESTE, "proyeccion": "EPSG:3857"},
        "ancho": ANCHO,
        "alto": ALTO,
        "capas": CAPAS,
        "archivo": "{fuente}/{hora}-{capa}.png (hora UTC AAAAMMDDHH)",
        "wrf": None,
        "icon": None,
    }
    errores = []
    incompletos = []

    if wrf:
        t0 = time.time()
        c = de_nombre(wrf)
        # La lluvia viene acumulada desde el inicio: la de una hora es la resta con la hora anterior.
        pedidos = sorted(set(plazos) | {p - 1 for p in plazos if p > 0})
        try:
            with ProcessPoolExecutor(8) as ex:
                campos = dict(ex.map(leer_wrf, [(c, p) for p in pedidos]))
            horas = []
            for p in plazos:
                t = c + dt.timedelta(hours=p)
                f = campos[p]
                if p > 0:
                    guardar(sal / "wrf", t, "lluvia", np.maximum(f["PP"] - campos[p - 1]["PP"], 0))
                horas.append(nombre(t))
            meta["wrf"] = {
                "corrida": wrf,
                "horas": horas,
                "capas": ["lluvia"],
                "sin_lluvia": [horas[0]] if plazos[0] == 0 else [],
                "fuente": "Pronóstico del SMN (modelo WRF)",
                "enlace": "https://registry.opendata.aws/smn-ar-wrf-dataset/",
            }
            print(f"WRF {wrf}: {len(horas)} horas en {time.time() - t0:.0f} s", file=sys.stderr)
        except Incompleto as e:
            incompletos.append(f"WRF {wrf}: {e}")
        except Exception as e:
            errores.append(f"WRF {wrf}: {e!r}")

    if icon:
        t0 = time.time()
        c = de_nombre(icon)
        try:
            indices = indices_icon(c)
            with ProcessPoolExecutor(4, initializer=_icon_inicio, initargs=(indices,)) as ex:
                campos = dict(ex.map(leer_icon, [(c, p) for p in plazos]))
            horas = []
            for p in plazos:
                t = c + dt.timedelta(hours=p)
                guardar(sal / "icon", t, "nubes", campos[p])
                horas.append(nombre(t))
            meta["icon"] = {
                "corrida": icon,
                "horas": horas,
                "capas": ["nubes"],
                "fuente": "DWD (ICON)",
                "licencia": "CC BY 4.0",
                "enlace": "https://opendata.dwd.de/weather/nwp/icon/",
            }
            print(f"ICON {icon}: {len(horas)} horas en {time.time() - t0:.0f} s", file=sys.stderr)
        except Exception as e:
            # El ICON caído no frena el WRF nuevo: quedan las nubes ya publicadas. Si tampoco se pueden copiar, falla.
            copia = copiar_icon(antes, sal)
            if copia:
                meta["icon"] = copia
                print(f"::warning::ICON {icon}: {e!r}; quedan las nubes de la corrida {copia['corrida']}", file=sys.stderr)
            else:
                errores.append(f"ICON {icon}: {e!r}")

    (sal / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (sal / ".nojekyll").write_text("")
    peso = sum(f.stat().st_size for f in sal.rglob("*") if f.is_file())
    print(f"salida {peso / 1e6:.1f} MB", file=sys.stderr)
    if errores:
        sys.exit("falló: " + "; ".join(errores))
    # Un archivo a medio subir no es una falla: esta vuelta no publica (queda lo anterior, sin mail) y la próxima, a
    # los 30 min, lo vuelve a intentar (ver sigue en «cambio» mientras no se publique la corrida nueva).
    listo = not incompletos
    if incompletos:
        print("::warning::sin publicar, el SMN todavía está subiendo: " + "; ".join(incompletos), file=sys.stderr)
    salida_gh = os.environ.get("GITHUB_OUTPUT")
    if salida_gh:
        with open(salida_gh, "a", encoding="utf-8") as f:
            f.write(f"listo={int(listo)}\n")


# --- medir (necesita requirements.txt) ---
#
# Cuánto coincidió el pronóstico con el radar, hora por hora. Solo mide: no corrige nada del modelo.
# - El radar: las imágenes de la hora ya limpias de ruido, como las ve la página (su /api/cuadro). Llovió en un punto
#   si alguna imagen de la hora lo pinta «Débil» o más (desde ~20 dBZ, ~0,6 mm/h).
# - El pronóstico: la lluvia de esa hora del WRF, de la última corrida que ya estaba publicada una hora antes de que
#   empiece (completa ~2 h 40 después de su hora y publicada hasta ~50 min más tarde). Lluvia es 0,5 mm o más, lo que
#   pinta el mapa.
# - Cada hora, lo que el radar ve bien (200 km) se parte en cajas de 20 km: una tormenta corrida unos km cuenta igual.
#   Por caja: los dos vieron lluvia (ambos), solo el pronóstico o solo el radar. Las cajas sin lluvia en ninguno no
#   cuentan: casi siempre no llueve y «acertó que no llovía» no dice nada.

#   Mientras Contingencias no da imágenes, la hora se mide con el radar de repuesto (RMA19 del SMN, en Santa Rosa), como
#   lo ve la página (su /api/rma19), alrededor de él y sin sus ecos fijos (rma19-fijos.png, ver «fijos»). La columna
#   «radar» dice cuál se usó: las dos medidas no son comparables entre sí (otro lugar, otro alcance, otro radar).

RADAR_API = "https://radar-mendoza.vercel.app/api"
FILTRO = 3  # La versión del filtro de ruido que pide la página (FILTRO en src/radar/cuadros.ts).
REPUESTO = 1  # La versión del repuesto que pide la página (REPUESTO en src/radar/cuadros.ts).
FIJOS = Path(__file__).with_name("rma19-fijos.png")
# La fecha de FIJOS: va en la columna «mascara» de cada hora medida con el repuesto, para saber cuáles se midieron con
# una máscara vieja (la del 10/11 salió de 1,5 días de registro y deja pasar ruido). Al rehacer FIJOS, cambiarla
# (test_la_mascara_dice_su_fecha lo recuerda).
MASCARA = "2026-11-10"
# Por radar, en el orden en que se eligen: dónde está, sus URL y cuántas imágenes por hora hacen falta (la mitad).
RADARES = {
    # ~30 imágenes por hora, ya limpias de ruido: la mitad de arriba de /api/cuadro.
    "contingencias": dict(lista=f"{RADAR_API}/cuadros", imagen=f"{RADAR_API}/cuadro?ruta={{}}&filtro={FILTRO}",
                          centro=(LAT, LON), minimo=15, mitad=True),
    # 6 por hora, crudas: la imagen entera de /api/rma19, con sus ecos fijos sacados (FIJOS).
    "rma19": dict(lista=f"{RADAR_API}/rma19", imagen=f"{RADAR_API}/rma19?ruta={{}}&v={REPUESTO}",
                  centro=(-33.259, -68.094), minimo=3, mitad=False),
}
# Colores de la escala del radar y su nivel (0 «Muy débil» … 4 «Posible granizo»), como src/radar/niveles.ts.
ESCALA_RADAR = [
    (0, (72, 61, 139)), (0, (0, 90, 0)), (1, (0, 112, 0)),
    (2, (8, 127, 219)), (2, (28, 71, 232)), (2, (110, 13, 198)),
    (3, (200, 15, 134)), (3, (192, 100, 135)), (3, (210, 136, 59)), (3, (250, 196, 49)),
    (4, (255, 253, 1)), (4, (254, 154, 88)), (4, (254, 95, 5)), (4, (253, 52, 28)), (4, (0, 0, 0)), (4, (211, 211, 211)),
]
NIVEL_LLUVIA = 1  # «Débil» o más.
LLUVIA_MM = 0.5
CAJA = 10  # celdas de 2 km por lado: cajas de 20 km.
# Hasta 200 km: más lejos el haz pasa alto y no ve la lluvia débil (del 6 al 7/11, entre 200 y 250 km el radar no vio
# lluvia en 30 horas y el modelo marcó 640 cajas: contaban como errores del pronóstico sin serlo).
ALCANCE_KM = 200
ESPERA_CORRIDA_H = 4
PARADO_H = 6  # una hora sin imágenes suficientes o que no bajaron se reintenta hasta esto; después queda sin radar.
TOPE_HORAS = 30  # por vuelta, para que una vuelta no se alargue.
COLUMNAS = ["hora", "corrida", "plazo", "cuadros", "ambos", "solo_pronostico", "solo_radar", "cajas", "km", "radar",
            "mascara"]


def _alcance(centro, fuera=None):
    # Las celdas a menos de ALCANCE_KM del radar (como dentroDelAlcance en la página), sin las de `fuera`, y la caja de
    # 20 km de cada una.
    import numpy as np

    MX, MY = _grilla_mercator()
    lat = np.degrees(2 * np.arctan(np.exp(MY / R)) - np.pi / 2)
    lon = np.degrees(MX / R)
    adentro = np.hypot((lat - centro[0]) * KLAT, (lon - centro[1]) * KLON) <= ALCANCE_KM
    if fuera is not None:
        adentro &= ~fuera
    fila, col = np.indices((ALTO, ANCHO))
    caja = (fila // CAJA) * math.ceil(ANCHO / CAJA) + col // CAJA
    total = math.ceil(ALTO / CAJA) * math.ceil(ANCHO / CAJA)
    # Una caja cuenta si al menos la mitad de sus celdas está dentro del alcance.
    dentro = np.bincount(caja[adentro], minlength=total) * 2 >= np.bincount(caja.ravel(), minlength=total)
    return adentro, caja, total, dentro


def _radar_lluvia(datos, mitad=True):
    # Una imagen de la página a la grilla de 2 km: True donde hay «Débil» o más. De /api/cuadro va la mitad de arriba
    # (lo limpio); de /api/rma19, la entera.
    import io

    import numpy as np
    from PIL import Image

    img = np.asarray(Image.open(io.BytesIO(datos)).convert("RGBA"))
    if mitad:
        img = img[: img.shape[0] // 2]
    alto, ancho = img.shape[:2]
    ys, xs = np.nonzero(img[..., 3] > 0)
    salida = np.zeros((ALTO, ANCHO), bool)
    if not len(ys):
        return salida
    rgb = img[ys, xs, :3].astype(np.int32)
    paleta = np.array([c for _, c in ESCALA_RADAR], np.int32)
    niveles = np.array([n for n, _ in ESCALA_RADAR])
    nivel = niveles[((rgb[:, None, :] - paleta[None]) ** 2).sum(-1).argmin(1)]
    si = nivel >= NIVEL_LLUVIA
    # La imagen va estirada sobre la misma caja en Mercator que la grilla: es una cuenta de proporción.
    salida[ys[si] * ALTO // alto, xs[si] * ANCHO // ancho] = True
    return salida


# --- fijos: los ecos fijos del repuesto ---
#
# El repuesto va crudo: la cordillera y el ruido alrededor del radar salen como lluvia casi siempre (en las primeras
# 34 imágenes, ~2400 celdas en el 90 % o más). Con eso, cada hora sumaría «solo radar» donde no llueve. Un eco fijo es
# una celda con lluvia en la mitad o más de las imágenes del registro (la rama rma19): ahí no se mide ni el radar ni el
# pronóstico. Para leer el registro, la imagen cruda se pasa como lo hace api/rma19.ts (aContingencias): la prueba
# test_rma19_como_la_pagina lo compara con lo que da la página.

FIJO = 0.5
RMA19_CAJA = (-35.43, -70.672, -31.089, -65.516)  # sur, oeste, norte, este de su imagen (RMA19 en api/rma19.ts)
ESCALA_SMN = [tuple(int(h[k:k + 2], 16) for k in (0, 2, 4)) for h in (
    "3d416b 3b4f78 3d5989 3d6595 3772a6 3189bb 25a1cf 4ce132 3ab027 237219 c9d333 d69818 c50017 c1005f cb00cd e2f5ee "
    "a7ecce 88dfbd").split()]
LLUVIA_DBZ = 20  # donde empieza «Débil» (ESCALA_DBZ en src/radar/niveles.ts)
CUADRO_ANCHO, CUADRO_ALTO = 500, 700  # el cuadro de Contingencias al que api/rma19.ts pasa la imagen
_dbz_vistos = {}


def _dbz_smn(rgb):
    # Como dbzDe de api/rma19.ts: el punto de su escala con el color más parecido, cada 0,1 dBZ (las mismas cuentas).
    if rgb not in _dbz_vistos:
        mejor, menor = -15, math.inf
        for k in range(len(ESCALA_SMN) - 1):
            a, z = ESCALA_SMN[k], ESCALA_SMN[k + 1]
            f = 0
            while f < 1:
                d = (a[0] + (z[0] - a[0]) * f - rgb[0]) ** 2 + (a[1] + (z[1] - a[1]) * f - rgb[1]) ** 2 \
                    + (a[2] + (z[2] - a[2]) * f - rgb[2]) ** 2
                if d < menor:
                    menor, mejor = d, -15 + (k + f) * 5
                f += 0.02
        _dbz_vistos[rgb] = mejor
    return _dbz_vistos[rgb]


def rma19_lluvia(datos):
    # Una imagen cruda del SMN a la grilla de 2 km, como /api/rma19 seguida de _radar_lluvia: True con LLUVIA_DBZ o más.
    import io

    import numpy as np
    from PIL import Image

    img = np.asarray(Image.open(io.BytesIO(datos)).convert("RGBA"))
    h, w = img.shape[:2]
    merc = lambda lat: math.log(math.tan(math.pi / 4 + lat * math.pi / 360))  # noqa: E731
    sur, oeste, norte, este = RMA19_CAJA
    m = merc(NORTE) + ((np.arange(CUADRO_ALTO) + 0.5) / CUADRO_ALTO) * (merc(SUR) - merc(NORTE))
    fy = np.floor(((m - merc(norte)) / (merc(sur) - merc(norte))) * h).astype(int)
    lon = OESTE + ((np.arange(CUADRO_ANCHO) + 0.5) / CUADRO_ANCHO) * (ESTE - OESTE)
    fx = np.floor(((lon - oeste) / (este - oeste)) * w).astype(int)
    cuadro = img[np.clip(fy, 0, h - 1)][:, np.clip(fx, 0, w - 1)]
    visible = (cuadro[..., 3] > 0) & ((fy >= 0) & (fy < h))[:, None] & ((fx >= 0) & (fx < w))[None]
    colores, cual = np.unique(cuadro[..., :3].reshape(-1, 3), axis=0, return_inverse=True)
    lluvia = np.array([_dbz_smn(tuple(int(v) for v in c)) >= LLUVIA_DBZ for c in colores])
    si = visible & lluvia[cual.reshape(visible.shape)]
    ys, xs = np.nonzero(si)
    salida = np.zeros((ALTO, ANCHO), bool)
    salida[ys * ALTO // CUADRO_ALTO, xs * ANCHO // CUADRO_ANCHO] = True
    return salida


def fijos(carpeta):
    # Rehace FIJOS con las imágenes de la rama rma19 (`AAAAMMDD/hhmm.png`) que estén en `carpeta`.
    import numpy as np
    from PIL import Image

    rutas = sorted(Path(carpeta).glob("*/*.png"))
    if not rutas:
        sys.exit(f"fijos: no hay imágenes en {carpeta}")
    veces = sum(rma19_lluvia(r.read_bytes()).astype(np.int32) for r in rutas)
    fijo = veces >= len(rutas) * FIJO
    Image.fromarray(fijo).save(FIJOS, optimize=True)
    print(f"fijos: {len(rutas)} imágenes ({rutas[0].parent.name}/{rutas[0].name} a {rutas[-1].parent.name}/"
          f"{rutas[-1].name}), {int(fijo.sum())} celdas fijas", file=sys.stderr)


def _corrida_para(hora):
    # La última corrida (00/06/12/18) que ya estaba publicada ESPERA_CORRIDA_H antes de la hora, con su plazo; si esa no
    # tiene el archivo, la anterior.
    tope = hora - dt.timedelta(hours=ESPERA_CORRIDA_H)
    c = tope.replace(hour=tope.hour // 6 * 6)
    for _ in range(3):
        plazo = int((hora - c).total_seconds() // 3600) + 1  # el archivo de H+1 trae la lluvia de H a H+1
        if pedir(url_wrf(c, plazo), tiempo=20, cabeza=True) is not None:
            return c, plazo
        c -= dt.timedelta(hours=6)
    return None, None


def _lista(url):
    # La lista de un radar (`{"AAAAMMDD": ["HHMMSS.png", …]}`, en UTC) como {hora: ruta}. Sin la lista (el radar o la
    # página caídos) no falla: queda vacía y sus horas se miden en otra vuelta, mientras sigan en ella. Queda como aviso
    # en la corrida (con el 403 de Contingencias, el de su lista sale en cada vuelta).
    try:
        lista = json.loads(pedir(url, tiempo=30) or b"{}")
    except (ValueError, OSError) as e:
        print(f"::warning::medir: sin la lista {url} ({e!r})", file=sys.stderr)
        return {}
    # Como leerLista de src/radar/cuadros.ts: la reflectividad es `HHMMSS.png` o, desde el 07/11/2026, `HHMMSS_dbz.png`
    # (la misma imagen). Granizo y manga no van, y una hora con los dos nombres cuenta una vez (el nuevo).
    por_hora = {}
    for dia, pngs in (lista.items() if isinstance(lista, dict) else []):
        for png in pngs if isinstance(pngs, list) else []:
            if not (re.fullmatch(r"\d{8}", dia) and isinstance(png, str) and re.fullmatch(r"\d{6}(_dbz)?\.png", png)):
                continue
            t = dt.datetime.strptime(dia + png[:6], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
            if t not in por_hora or png.endswith("_dbz.png"):
                por_hora[t] = f"{dia}/{png}"
    return por_hora


def _leer_filas(ruta):
    import csv

    if not ruta.exists():
        return []
    with ruta.open(encoding="utf-8", newline="") as f:
        # Las filas medidas con otro alcance se descartan: se vuelven a medir las que sigan en la lista del radar.
        filas = [x for x in csv.DictReader(f) if x.get("km") == str(ALCANCE_KM)]
    for x in filas:
        if x.get("radar") is None:  # de antes del repuesto: todas con Contingencias
            x["radar"] = "contingencias" if x["corrida"] else ""
        if x.get("mascara") is None:  # de antes de la columna: el repuesto, con la primera máscara
            x["mascara"] = "2026-11-10" if x["radar"] == "rma19" else ""
    return filas


def medir(archivo):
    import csv
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

    import numpy as np
    from PIL import Image

    ruta = Path(archivo)
    filas = _leer_filas(ruta)
    hechas = {f["hora"] for f in filas}
    ahora = dt.datetime.now(dt.timezone.utc)

    listas = {n: _lista(r["lista"]) for n, r in RADARES.items()}
    tiempos = sorted(t for lista in listas.values() for t in lista)
    if not tiempos:
        print("medir: ningún radar dio imágenes", file=sys.stderr)
        return
    # Desde la primera hora entera de las listas hasta la última terminada hace 40 min: las imágenes llegan con atraso
    # (las del repuesto, hasta ~40 min después de su hora).
    h = tiempos[0].replace(minute=0, second=0) + (dt.timedelta(hours=1) if tiempos[0].minute >= 2 else dt.timedelta())
    ultima = (ahora - dt.timedelta(minutes=100)).replace(minute=0, second=0, microsecond=0)
    horas = []
    while h <= ultima:
        if nombre(h) not in hechas:
            horas.append(h)
        h += dt.timedelta(hours=1)
    horas = horas[-TOPE_HORAS:]
    if not horas:
        print("medir: nada nuevo", file=sys.stderr)
        return

    # Por radar, las celdas que cuentan y la caja de cada una. Al repuesto se le sacan sus ecos fijos.
    fuera = np.asarray(Image.open(FIJOS)).astype(bool)
    alcances = {n: _alcance(r["centro"], fuera if n == "rma19" else None) for n, r in RADARES.items()}

    def bajar(url, mitad):
        try:
            return _radar_lluvia(pedir(url, tiempo=30), mitad)
        except Exception:
            return None

    # Cada hora va con el primer radar que tenga imágenes suficientes; sin ninguno, se reintenta hasta PARADO_H y
    # después queda sin radar (con las imágenes del que más tuvo).
    elegido = {}
    for h in horas:
        por_radar = {n: [r for t, r in sorted(lista.items()) if h <= t < h + dt.timedelta(hours=1)]
                     for n, lista in listas.items()}
        n = next((n for n in RADARES if len(por_radar[n]) >= RADARES[n]["minimo"]), None)
        elegido[h] = (n, por_radar[n] if n else max(por_radar.values(), key=len))

    # Un archivo del WRF que no se pudo leer (o el S3 que no contesta) no tira la medición entera: esa hora se
    # reintenta en otra vuelta y, pasadas PARADO_H, queda sin corrida.
    def corrida_o_nada(h):
        try:
            return _corrida_para(h)
        except Exception as e:
            print(f"medir: sin la corrida de {nombre(h)} ({e!r})", file=sys.stderr)
            return None, None

    corridas = {h: corrida_o_nada(h) if elegido[h][0] else (None, None) for h in horas}
    archivos = sorted({(c, p) for c, plazo in corridas.values() if c for p in (plazo - 1, plazo)})
    with ProcessPoolExecutor(4) as ex:
        wrf = {cp: pp for cp, pp in zip(archivos, ex.map(_leer_pp, archivos)) if pp is not None}

    nuevas = []
    for h in horas:
        n, rutas = elegido[h]
        vieja = ahora - h > dt.timedelta(hours=PARADO_H + 1)
        fila = {"hora": nombre(h), "corrida": "", "plazo": "", "cuadros": len(rutas), "km": ALCANCE_KM, "radar": "",
                "mascara": ""}
        if n is None and not vieja:
            continue  # ningún radar con imágenes suficientes (el repuesto puede darlas tarde): se reintenta
        c, plazo = corridas[h]
        if c is not None and not {(c, plazo - 1), (c, plazo)} <= wrf.keys():
            if not vieja:
                continue  # un archivo del WRF no se pudo leer: se reintenta
            c = None
        if n and c is None and not vieja:
            continue  # todavía no está la corrida en S3: se reintenta
        if n and c is not None:
            r = RADARES[n]
            adentro, caja, total, dentro = alcances[n]
            caja_ok = dentro[caja] & adentro  # celdas que cuentan
            with ThreadPoolExecutor(8) as ex:
                imagenes = [x for x in ex.map(lambda ruta: bajar(r["imagen"].format(ruta), r["mitad"]), rutas)
                            if x is not None]
            if len(imagenes) < len(rutas) * 0.8 and not vieja:
                continue  # fallaron pedidos: se reintenta en la próxima vuelta
            fila["cuadros"] = len(imagenes)
            if len(imagenes) >= r["minimo"]:
                radar = np.logical_or.reduce(imagenes) & caja_ok
                pron = (np.maximum(wrf[(c, plazo)] - wrf[(c, plazo - 1)], 0) >= LLUVIA_MM) & caja_ok
                en_radar = np.bincount(caja[radar], minlength=total) > 0
                en_pron = np.bincount(caja[pron], minlength=total) > 0
                fila.update(
                    corrida=nombre(c), plazo=plazo,
                    ambos=int((en_radar & en_pron).sum()),
                    solo_pronostico=int((en_pron & ~en_radar).sum()),
                    solo_radar=int((en_radar & ~en_pron).sum()),
                    cajas=int(dentro.sum()),
                    radar=n,
                    mascara=MASCARA if n == "rma19" else "",
                )
        nuevas.append(fila)
        print(f"medir {fila}", file=sys.stderr)

    filas = sorted(filas + nuevas, key=lambda f: f["hora"])
    with ruta.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, COLUMNAS, lineterminator="\n")
        w.writeheader()
        w.writerows(filas)


def _leer_pp(args):
    # La lluvia de un plazo, o None si no se pudo leer (medir reintenta esa hora en otra vuelta).
    try:
        return leer_wrf(args)[1]["PP"]
    except Exception as e:
        print(f"medir: WRF {nombre(args[0])} plazo {args[1]} sin leer ({e!r})", file=sys.stderr)
        return None


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["medir"]:
        medir(a[1])
    elif a[:1] == ["fijos"]:
        fijos(a[1])
    elif a[:1] == ["ver"]:
        ver(a[1], "--forzar" in a)
    elif a[:1] == ["armar"]:
        opc = dict(zip(a[2::2], a[3::2]))
        plazos = [int(x) for x in opc["--plazos"].split(",")] if "--plazos" in opc else list(PLAZOS)
        armar(a[1], opc.get("--wrf"), opc.get("--icon"), plazos, opc.get("--antes"))
    else:
        sys.exit("uso: ver URL | armar SALIDA --wrf AAAAMMDDHH --icon AAAAMMDDHH [--antes URL] | medir acierto.csv | fijos CARPETA_RMA19")
