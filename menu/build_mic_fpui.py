#!/usr/bin/env python3
"""La fila "Mic" AUTO / INT como bloque FPUI (fpSup/uishare/ui): lo que la fila AÑADE a
la pantalla original B2_6, compuesto en la cámara por uishare/ui_apply.c.

build_mic_page.py hace la página entera; aquí se convierte en adiciones y se demuestra,
con el aplicador de referencia de Bei, que aplicarlas a la página original da
exactamente esa página (cadenas privadas comparadas por texto, como las resuelve la
cámara).

Igual que fpSup/uishare/ui/rows.py row_block, salvo una cosa: B2_6 no tiene grupo
ModeChange (sus filas se ven en todos los modos), así que no se añade ninguna raíz a
esa lista. El bloque no toca ficheros ni engancha nada: el donante tiene hijos fijos.

    python3 -B build_mic_fpui.py --out FICHERO
"""
import argparse
import os
import hashlib
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
FPSUP = pathlib.Path(os.environ['FPSUP_REPO'])
sys.path.insert(0, str(FPSUP / 'uishare'))
sys.path.insert(0, str(FPSUP / 'lossless/menu'))
sys.path.insert(0, str(HERE))
from ui import fpui, rows          # noqa: E402
import pack_menu_file as P         # noqa: E402  (stock_at: la pista del pool compartido)
import build_menu_candidate as BM  # noqa: E402
import build_mic_page as M         # noqa: E402

NAME = 'B2_6'
COUNTER = 'rows'                   # filas añadidas a B2_6: y 162, después 243...


def u32(b, o):
    return struct.unpack_from('>I', b, o)[0]


def row_block_no_mode(name, entry_offset, stock_page, page, manifest, pool_stock, stock_at,
                      insert_at, parent_at, y_role, row_base_y, counter):
    """rows.row_block sin ModeChange. Devuelve (Block, info)."""
    clone_locs = [r for r in manifest['record_locations'] if r['provenance'].startswith('cloned')]
    fpui.check(clone_locs, 'no hay registros copiados en el manifiesto')
    start = clone_locs[0]['candidate_offset']
    length = sum(r['length'] for r in clone_locs)
    stock_before = {r['source_record']: r['length'] for r in manifest['record_locations']
                    if not r['provenance'].startswith('cloned')
                    and int(r['source_record'], 16) - 0xC0000000 - rows.PAGE_BASE(manifest) < insert_at}
    shift = sum(stock_before.values()) - insert_at
    fpui.check(start == insert_at + shift and
               all(a['candidate_offset'] + a['length'] == b['candidate_offset']
                   for a, b in zip(clone_locs, clone_locs[1:])),
               'los registros copiados no son un bloque en el punto de inserción')
    frag = bytearray(page[start:start + length])
    where = {r['source_record']: r['candidate_offset'] - start for r in clone_locs}

    id_map = {int(k): v for k, v in manifest['new_row']['object_id_map'].items()}
    first = min(id_map.values())
    fpui.check(sorted(id_map.values()) == list(range(first, first + len(id_map))),
               'los ids nuevos no son una serie')

    blk = fpui.Block()
    relocs = []
    for f in manifest['typed_object_fields']:
        if f['scope'] == 'cloned_object' and f['source_record'] in where:
            at = where[f['source_record']] + f['field_offset']
            fpui.check(u32(frag, at) == f['new'], 'campo de objeto distinto')
            relocs.append((fpui.R_LOCAL_ID, f['new'] - first, at))
    for f in manifest['typed_string_fields']:
        if f['source_record'] in where and f['pool_offset'] >= len(pool_stock):
            at = where[f['source_record']] + f['field_offset']
            relocs.append((fpui.R_STRING, blk.string(f['text'], stock_at(f['text'])), at))
    y = [c for c in manifest['typed_changes'] if c['role'] == y_role]
    fpui.check(len(y) == 1, 'la fila necesita exactamente un campo Y')
    at = where[y[0]['source_record']] + y[0]['field_offset']
    fpui.check(u32(frag, at) == fpui.int_to_f32(row_base_y), 'la Y no es la primera posición')
    relocs.append((fpui.R_SLOT_F32, 0, at))
    relocs = sorted(set(relocs), key=lambda r: r[2])
    fpui.check(len({r[2] for r in relocs}) == len(relocs), 'dos reubicaciones en un campo')

    s_recs, p_recs = rows.records(stock_page), rows.records(page)
    sh, ph = s_recs[0][1], p_recs[0][1]
    comps, arrays = rows.header_layout(sh)
    pcomps, parrays = rows.header_layout(ph)
    fpui.check(len(comps) == len(pcomps), 'cambiaron los tipos de componente')
    header_ops, grow = [(fpui.OP_ADD32, 8, u32(ph, 8) - u32(sh, 8))], 0
    for so, po in zip(comps, pcomps):
        fpui.check(u32(sh, so - 4) == u32(ph, po - 4), 'cambió el orden de los componentes')
        if u32(ph, po) != u32(sh, so):
            header_ops.append((fpui.OP_ADD32, so, u32(ph, po) - u32(sh, so)))
    inserts = []
    for (scount, send, w), (pcount, pend, pw) in zip(arrays, parrays):
        n_s, n_p = u32(sh, scount), u32(ph, pcount)
        fpui.check(n_p >= n_s and sh[scount + 4:send] == ph[pcount + 4:pcount + 4 + (send - scount - 4)],
                   'cambió una entrada de presupuesto original')
        if n_p > n_s:
            added = ph[pend - 4 * w * (n_p - n_s):pend]
            inserts.append((send, bytes(added)))
            header_ops.append((fpui.OP_ADD32, scount, n_p - n_s))
            grow += len(added)
    header_ops.append((fpui.OP_ADD32, 4, grow))

    page_name = blk.string(name, fpui.NAME)
    blk.op(fpui.OP_PAGE, page_name, entry_offset, len(stock_page), first, len(id_map),
           length + grow, len(inserts) + 1)
    for pos in (0, insert_at, parent_at):                 # las etiquetas de registro siguen ahí
        blk.op(fpui.OP_GUARD, pos, u32(stock_page, pos))
    blk.op(fpui.OP_ALLOC, blk.string(name + '.' + counter, fpui.NAME), row_base_y, rows.ROW_STEP)
    for code, pos, delta in header_ops:
        blk.op(code, pos, delta)
    for pos, data in inserts:
        blk.op(fpui.OP_INSERT, pos, blk.fragment(data), len(data))
    off = blk.fragment(bytes(frag))
    args = []
    for kind, arg, at in relocs:
        args += [kind << 24 | arg, at]
    blk.op(fpui.OP_INSERT, insert_at, off, length, *args)
    blk.op(fpui.OP_ADD32, parent_at + 16, 1)
    blk.op(fpui.OP_DONE)
    info = {'page': name, 'objects': len(id_map), 'first_local_id': first, 'row_bytes': length,
            'header_grow': grow, 'relocations': len(relocs), 'strings': [t for t, _ in blk.strings]}
    return blk, info


def build(seg0=None):
    seg0 = M.SEG0.read_bytes() if seg0 is None else seg0
    page, _pool, manifest = M.build(seg0, BM.load_audit(M.AUDIT))
    stock_page = seg0[M.PAGE_START:M.PAGE_END]
    pool_stock = seg0[P.STOCK_POOL - 0xC0000000:][:P.STOCK_POOL_LEN]
    hint = lambda s: P.stock_at(pool_stock, s.encode())
    blk, info = row_block_no_mode(NAME, M.ENTRY_OFFSET, stock_page, page, manifest, pool_stock, hint,
                                  insert_at=M.INSERT_AT - M.PAGE_START,
                                  parent_at=M.MENU_RECORD - M.PAGE_START,
                                  y_role='mic_row_y', row_base_y=int(M.ROW_Y), counter=COUNTER)
    blob = blk.encode()
    rows.prove_same(blob, NAME, stock_page, page, manifest, pool_stock, hint)
    _, ops, _ = fpui.decode(blob)
    fpui.check(not any(code in (fpui.OP_HOOK_FV, fpui.OP_FILE) for code, _ in ops),
               'el bloque de Mic no debe tocar ficheros ni enganchar nada')
    return blob, info, (stock_page, pool_stock, page, manifest)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', type=pathlib.Path, required=True)
    a = ap.parse_args()
    blob, info, _ = build()
    fpui.check(not a.out.exists(), 'el fichero de salida ya existe')
    a.out.write_bytes(blob)
    print(f'{a.out}: {len(blob)} bytes, sha256 {hashlib.sha256(blob).hexdigest()}')
    print(info)


if __name__ == '__main__':
    main()
