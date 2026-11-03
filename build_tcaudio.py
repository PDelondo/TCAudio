#!/usr/bin/env python3
"""TCAudio para Loader v4: native/tcaudio.c. SPDX-License-Identifier: GPL-3.0-or-later

    FPSUP_REPO=/ruta/a/fpSup python3 -B build_tcaudio.py --out DIR

Necesita el firmware 5.02 extraído por tu cuenta: las filas se componen contra la página original.
"""
import argparse
import hashlib
import os
import pathlib
import struct
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = pathlib.Path(os.environ['FPSUP_REPO'])
FPUI = REPO / 'fpsup-ui'
sys.path.insert(0, str(REPO / 'loader-v4'))
import build_v4 as B4      # noqa: E402

NAME = 'TCAudio_v1.2.0.BIN'
DISPLAY = 'TC Audio'
SUP_ID = 'TCAU'


def menu_blocks():
    """Las filas Audio Input e Int+Ext Mics: bloques FPUI de menu/ contra el firmware 5.02. La segunda
    es la misma copia con otros textos; el cargador de filas las apila solas."""
    sys.path.insert(0, str(HERE / 'menu'))
    sys.path.insert(0, str(REPO / 'fp_usb_shell'))
    import build_mic_fpui
    M = build_mic_fpui.M

    def row(title, labels, variable, name):
        old = {'Mic': title, M.LABELS[0]: labels[0], M.LABELS[1]: labels[1], M.VALUE_VARIABLE: variable}
        saved = M.PRIVATE_FIELDS, M.TITLE, M.LABELS, M.VALUE_VARIABLE, M.ROW_NAME
        M.PRIVATE_FIELDS = {k: (o, s, old.get(n, n)) for k, (o, s, n) in saved[0].items()}
        M.TITLE, M.LABELS, M.VALUE_VARIABLE, M.ROW_NAME = title, labels, variable, name
        try:
            return build_mic_fpui.build()[0]
        finally:
            M.PRIVATE_FIELDS, M.TITLE, M.LABELS, M.VALUE_VARIABLE, M.ROW_NAME = saved

    return (row('Audio Input', ('Auto', 'Internal'), 'MV_fpMicInt', 'fpMicInt_Row'),
            row('Int+Ext Mics', ('Off', 'On'), 'MV_fpMicMix', 'fpMicMix_Row'))


def sup():
    """Como B4.c_sup, con los bloques de las filas tras el código."""
    mic, mix = menu_blocks()
    with tempfile.TemporaryDirectory(prefix='tcaudio-') as d:
        text = B4.compile_unit(pathlib.Path(d), HERE / 'native' / 'tcaudio.c', 'sup_entry',
                               ['-I', str(HERE / 'native'), '-I', str(FPUI)])
    body = bytearray(text.code + b'\0' * (-len(text.code) % 4))
    table = body.index(struct.pack('<I', 0x4D4F4455))           # tca_ui, tras tca_ctx
    spans = []
    for block in (mic, mix):
        spans += [len(body) - (table - 4), len(body) + len(block) - (table - 4)]
        body += block + b'\0' * (-len(block) % 4)
    struct.pack_into('<4I', body, table, *spans)
    entry = B4.SL4_HEADER_LEN + text.functions['sup_entry']
    blob = B4.make_sup(bytes(body), entry, SUP_ID, version=1, flags=B4.SL_F_CACHED_OK, name=DISPLAY,
                       min_svc=B4.SL4_SVC_VERSION, block_size=None)
    size = len(blob) + 0xC00
    return blob[:12] + struct.pack('<I', size + (-size % 4)) + blob[16:]


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=pathlib.Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    data = sup()
    (a.out / NAME).write_bytes(data)
    print(a.out / NAME, len(data), hashlib.sha256(data).hexdigest()[:16])
