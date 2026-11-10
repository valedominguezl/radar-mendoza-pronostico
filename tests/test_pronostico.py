# Pruebas de lo que no debe tirar una vuelta: un modelo que no contesta, un plazo que todavía no subió, un archivo
# roto al medir. Uso: python -m unittest discover -s tests   (con requirements.txt instalado)
import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pronostico  # noqa: E402

PUBLICADO = {"wrf": {"corrida": "2026111000"}, "icon": {"corrida": "2026111000", "horas": ["2026111000", "2026111001"]}}


def correr_ver(ultima, meta):
    salida, errores = io.StringIO(), io.StringIO()
    with mock.patch.object(pronostico, "ultima", ultima), \
            mock.patch.object(pronostico, "pedir", lambda url, **k: meta), \
            contextlib.redirect_stdout(salida), contextlib.redirect_stderr(errores):
        pronostico.ver("https://x/meta.json", False)
    return dict(l.split("=", 1) for l in salida.getvalue().split()), errores.getvalue()


def icon_caido(completa, ahora):
    # El ICON no contesta; el WRF tiene una corrida nueva.
    c = dt.datetime(2026, 11, 10, 6, tzinfo=dt.timezone.utc)
    if "dwd" in completa(c):
        raise OSError("503")
    return c


class Ver(unittest.TestCase):
    def test_icon_caido_no_frena_el_wrf(self):
        o, err = correr_ver(icon_caido, json.dumps(PUBLICADO).encode())
        self.assertEqual(o["wrf"], "2026111006")
        self.assertEqual(o["icon"], "2026111000")  # la publicada
        self.assertEqual(o["cambio"], "1")
        self.assertIn("::warning::ICON", err)

    def test_sin_lo_publicado_y_un_modelo_caido_no_cambia(self):
        o, _ = correr_ver(icon_caido, None)
        self.assertEqual(o["cambio"], "0")


class LeerWrf(unittest.TestCase):
    def test_plazo_con_404_es_incompleto(self):
        import numpy as np

        sistema = mock.Mock()
        sistema.open.side_effect = FileNotFoundError("404")
        with mock.patch.object(pronostico, "_LCC", (np.zeros(1), np.zeros(1))), \
                mock.patch("fsspec.filesystem", return_value=sistema), mock.patch("time.sleep"):
            with self.assertRaises(pronostico.Incompleto):
                pronostico.leer_wrf((dt.datetime(2026, 11, 10, tzinfo=dt.timezone.utc), 5))


class Medir(unittest.TestCase):
    def test_un_archivo_roto_no_tira_la_medicion(self):
        with mock.patch.object(pronostico, "leer_wrf", side_effect=OSError("roto")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(pronostico._leer_pp((dt.datetime(2026, 11, 10, tzinfo=dt.timezone.utc), 5)))


class CopiarIcon(unittest.TestCase):
    def test_copia_las_nubes_publicadas(self):
        def pedir(url, **k):
            return json.dumps(PUBLICADO).encode() if url.endswith("meta.json") else b"png " + url.encode()

        with tempfile.TemporaryDirectory() as d, mock.patch.object(pronostico, "pedir", pedir):
            sal = Path(d)
            (sal / "icon").mkdir()
            icon = pronostico.copiar_icon("https://x/sitio/meta.json", sal)
            self.assertEqual(icon["corrida"], "2026111000")
            self.assertEqual((sal / "icon" / "2026111001-nubes.png").read_bytes(), b"png https://x/sitio/icon/2026111001-nubes.png")

    def test_si_falta_una_imagen_no_copia(self):
        def pedir(url, **k):
            return json.dumps(PUBLICADO).encode() if url.endswith("meta.json") else None

        with tempfile.TemporaryDirectory() as d, mock.patch.object(pronostico, "pedir", pedir):
            (Path(d) / "icon").mkdir()
            self.assertIsNone(pronostico.copiar_icon("https://x/sitio/meta.json", Path(d)))


if __name__ == "__main__":
    unittest.main()
