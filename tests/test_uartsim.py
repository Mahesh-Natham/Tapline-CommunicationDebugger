from tapline.uartsim import reinterpret
from tapline.model import SerialConfig as C


DATA = bytes(range(256)) + b"hello world 5A A5"


def test_reinterpret_same_config_is_identity():
    for cfg in [C("x", 9600), C("x", 115200, 8, "E", 1), C("x", 19200, 7, "E", 1), C("x", 57600, 8, "N", 2)]:
        data = DATA if cfg.bytesize == 8 else bytes(b & 0x7F for b in DATA)
        got, err = reinterpret(data, cfg, cfg)
        assert got == data and err == 0


def test_reinterpret_wrong_baud_garbles():
    got, err = reinterpret(DATA, C("x", 9600), C("x", 115200))
    assert got != DATA
    got, err = reinterpret(DATA, C("x", 115200), C("x", 9600))
    assert got != DATA and err > 0


def test_reinterpret_parity_mismatch_counts_errors():
    got, err = reinterpret(DATA, C("x", 9600, 8, "N", 1), C("x", 9600, 8, "E", 1))
    assert err > 0
    got0, _ = reinterpret(DATA, C("x", 9600, 8, "N", 1), C("x", 9600, 8, "E", 1), errors_as_zero=True)
    assert got0.count(0) > DATA.count(0)
