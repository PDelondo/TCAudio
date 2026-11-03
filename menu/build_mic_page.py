#!/usr/bin/env python3
"""Construye la pantalla B2_6 (submenú de audio) con una tercera fila "Mic" Auto / Internal.

B2_6 = Grabación de audio ON > submenú: B2_6_1 Ajuste de ganancia, B2_6_2 Cancelador
del ruido del viento. La fila nueva es la tercera, y 162, ligada a la variable privada
MV_fpMicInt (0 AUTO, 1 INT), registrada por el módulo en el registro de la UI: solo en
memoria, 0 en cada arranque, nunca en la zona de ajustes.

Método (fp-native-ui §8b/§8c, igual que Lossless y ScreenFlip): copiar una fila de la
MISMA página. Donante: B2_6_2 (id 91), un MenuItem_Toggle con hijos fijos, sin CSV.
Su subárbol es un tramo continuo de registros y ningún registro de fuera lo cita (los
únicos objetos de fuera que cita son la raíz de la página, 1, el Footer, 37, y su padre
Menu, 89). La página no tiene grupo ModeChange: sus filas se ven en todos los modos.

Cambios en la copia, cada uno comprobado contra los bytes originales:
  raíz B2_6_2 -> fpMicInt_Row; y 81 -> 162;
  MV_AUDIO_NoiseCanceller (4 campos) -> MV_fpMicInt;
  título drawText 0354 -> texto literal "Mic";
  etiquetas 0080 / 0081 (resumen y popup) -> literales "Auto" / "Camera Mics".
En la página: cabecera ampliada, Menu (89) pasa de 3 a 4 hijos.

Base: fpSup/screenflip/menu/build_flip_page.py (Bei, GPL-3.0). Sin cámara.
"""
import argparse
import os
import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
FPSUP = Path(os.environ['FPSUP_REPO'])
ROOT = FPSUP.parent
sys.path.insert(0, str(FPSUP / 'lossless/menu'))
sys.path.insert(0, str(FPSUP / 'screenflip/menu'))
sys.path.insert(0, str(HERE))
import build_menu_candidate as BM   # noqa: E402
import build_flip_page as FP        # noqa: E402  (owners, subtree)
import remapper_b26                 # noqa: E402

SEG0 = ROOT / 'out/seg0_c0000000.bin'
AUDIT = ROOT / 'research/ui/tools/native_ui_audit.py'
NAMES = ('B2_6.fpMicInt.page', 'B2_6.fpMicInt.strings', 'manifest.json')

# ---- datos fijos de Ver.5.02 (posiciones en seg0, base 0xC0000000) -----------
DECLARATION = 0x18EC9BC            # declaración de pantalla "B2_6" (etiqueta 10001)
ENTRY_OFFSET = 0x1B8E9B            # desplazamiento de la página en el NBU
PAGE_START, PAGE_END = 0x1A792FB, 0x1A88E92      # incluye el FFFFFFFF/8 final
DONOR_START, DONOR_END = 0x1A852C1, 0x1A876F4    # subárbol de B2_6_2, registros enteros
DONOR_ROOT, DONOR_PARENT = 91, 89
MENU_RECORD = 0x1A80F85            # objeto Menu (89): 3 hijos (Blind, B2_6_1, B2_6_2)
INSERT_AT = 0x1A876F4              # el objeto Footer: la copia va justo después de B2_6_2
ROOT_OBJECTBASE = 0x1A852E5        # objectBase de 91: máscara 1 (position), y en +36
DONOR_Y, ROW_Y = 81.0, 162.0
# se fijan las huellas en la primera ejecución (ver --pin)
PAGE_SHA256 = '21e40373fcce7c4acb2bce353ed4658ad8adbeb97170f5951546a8b9a6804b83'
DONOR_SHA256 = '34348fd4c481fa76508a79153e144c425e20d6186bf1b4d1e6cbb0f3d52c420e'

VALUE_VARIABLE, ROW_NAME = 'MV_fpMicInt', 'fpMicInt_Row'
TITLE = 'Mic'
LABELS = ('Auto', 'Internal')      # valor 0, 1 (nombre elegido por el usuario)

# registro -> (desplazamiento del campo, texto original, texto privado)
PRIVATE_FIELDS = {
    0x1A858A3: (40, 'MV_AUDIO_NoiseCanceller', VALUE_VARIABLE),
    0x1A861C8: (40, 'MV_AUDIO_NoiseCanceller', VALUE_VARIABLE),
    0x1A86496: (36, 'MV_AUDIO_NoiseCanceller', VALUE_VARIABLE),
    0x1A8652A: (36, 'MV_AUDIO_NoiseCanceller', VALUE_VARIABLE),
    0x1A85D76: (32, '0080', LABELS[0]),          # resumen Icon01
    0x1A85EFB: (32, '0081', LABELS[1]),          # resumen Icon02
    0x1A86B69: (32, '0080', LABELS[0]),          # popup 01 Text
    0x1A86EBE: (32, '0081', LABELS[1]),          # popup 02 Text
    0x1A872E7: (32, '0354', TITLE),              # Name (título de la fila)
    # Petición de foco: submenu_02 es la de la fila del viento, cuyo popup el código
    # nativo hace estrecho (On/Off); submenu_01 es la de la ganancia, más ancha.
    # Hipótesis (24-10-2026, el usuario vio los textos comprimidos): pedir la anchura
    # de la primera fila. Sin probar qué más hace el nativo con submenu_01.
    0x1A85417: (36, 'submenu_02', 'submenu_01'),
}
LITERAL_RECORDS = [0x1A85D76, 0x1A85EFB, 0x1A86B69, 0x1A86EBE, 0x1A872E7]
# Se conservan a propósito, como hicieron Lossless y ScreenFlip: la petición de foco de
# la fila y los conjuntos de animación compartidos de la página.
KEPT_STRINGS = {'submenu_01', 'submenu_width_01', 'submenu_width_02', 'Footer', 'Footer05'}


def f32(v):
    return struct.unpack('>I', struct.pack('>f', v))[0]


def check(condition, message):
    if not condition:
        raise BM.CandidateError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def schemas_for(source):
    """Esquemas de Bei más toggleVisible, leído del firmware: un único campo
    `select-value` de tipo 0 (entero), descriptor nativo en 0xC2E02BD0."""
    s = BM.load_property_schemas(source)
    kind, name_ptr, default = struct.unpack_from('<III', source, 0xC2E02BD0 - 0xC0000000)
    n = source[name_ptr - 0xC0000000:source.index(b'\0', name_ptr - 0xC0000000)]
    check(kind == 0 and n == b'select-value', 'descriptor de toggleVisible inesperado')
    s['toggleVisible'] = [{'kind': 0, 'name': 'select-value', 'default_raw': default}]
    return s


def build(source, audit, pinned=True):
    check(len(source) == audit.SEG0_SIZE and digest(source) == audit.SEG0_SHA256,
          'la entrada no es el seg0 fijado de Ver.5.02')
    pool = BM.PrivatePool(source[audit.POOL:audit.POOL_END])
    check(pool.resolve(BM.u32(source, DECLARATION + 4)) == 'B2_6', 'nombre de la declaración B2_6')
    check(BM.u32(source, DECLARATION + 12) == ENTRY_OFFSET and audit.NBU + ENTRY_OFFSET == PAGE_START,
          'desplazamiento de la declaración B2_6')
    if pinned:
        check(digest(source[PAGE_START:PAGE_END]) == PAGE_SHA256, 'huella de la página B2_6')
        check(digest(source[DONOR_START:DONOR_END]) == DONOR_SHA256, 'huella del donante B2_6_2')

    page = audit.records(source, PAGE_START, PAGE_END)
    check((page[-1].tag, len(page[-1].data)) == (0xFFFFFFFF, 8), 'la página no acaba en FFFFFFFF/8')
    header = audit.parse_header(page[0], pool.resolve)
    objects = audit.parse_objects(page, pool.resolve)
    check(header['objects'] == len(objects), 'presupuesto de objetos')
    groups = [r for r in page if r.tag == 0x1000B]
    check(len(groups) == len(header['groups']), 'presupuesto de grupos')
    by_offset = {r.offset: r for r in page}
    check(all(a in by_offset for a in (DONOR_START, DONOR_END, INSERT_AT, MENU_RECORD, ROOT_OBJECTBASE)),
          'un límite fijado no es un registro entero')

    # ---- el menú y sus filas ---------------------------------------------------
    menu = objects[DONOR_PARENT]
    check((menu['name'], menu['child_count'], by_offset[MENU_RECORD].word(20)) == ('Menu', 3, DONOR_PARENT),
          'identidad del objeto Menu')
    check(sorted(objects[i]['name'] for i, o in objects.items() if o['parent_id'] == DONOR_PARENT) ==
          ['B2_6_1', 'B2_6_2', 'Blind'], 'hijos de Menu')
    check(by_offset[INSERT_AT].tag == 0x10003 and objects[by_offset[INSERT_AT].word(20)]['name'] == 'Footer',
          'B2_6_2 no va seguida del Footer')

    # ---- el donante -------------------------------------------------------------
    owner_of = FP.owners(page)
    donor_ids = FP.subtree(objects, DONOR_ROOT)
    check(objects[DONOR_ROOT]['name'] == 'B2_6_2', 'raíz del donante')
    inside = [i for i, r in enumerate(page) if DONOR_START <= r.offset < DONOR_END]
    check(all(owner_of[i] in donor_ids for i in inside), 'el tramo del donante tiene un registro ajeno')
    check(all(DONOR_START <= r.offset < DONOR_END for i, r in enumerate(page)
              if 0x10003 <= r.tag < 0x11000 and owner_of[i] in donor_ids),
          'un registro del donante está fuera de su tramo')
    check(not any(r.tag == 0x10008 for r in page[inside[0]:inside[-1] + 1]),
          'el donante tiene lista CSV: este constructor espera hijos fijos')
    # ningún registro de fuera cita un objeto del donante: búsqueda tipada (la de
    # ScreenFlip), y en los registros que no tienen esquema, palabra a palabra en cada byte
    schemas = schemas_for(source)
    # external_references usa los límites del donante como globales de su módulo
    FP.DONOR_START, FP.DONOR_END = DONOR_START, DONOR_END
    hits, unscanned, _ = FP.external_references(page, pool, objects, donor_ids, schemas)
    check(not hits, 'registros de fuera citan al donante: %r' % hits[:4])
    for u in unscanned:
        r = by_offset[int(u['record'], 16) - 0xC0000000]
        raw = {BM.u32(r.data, k) for k in range(0, len(r.data) - 3)} & donor_ids
        check(not raw, 'el registro sin esquema %s cita al donante: %r' % (u['record'], sorted(raw)))
    base = by_offset[ROOT_OBJECTBASE].data
    check(pool.resolve(BM.u32(base, 8)) == 'objectBase' and BM.u32(base, 20) == DONOR_ROOT and
          BM.u32(base, 28) == 1 and BM.u32(base, 32) == 0 and BM.u32(base, 36) == f32(DONOR_Y),
          'el donante no está en y 81')

    # ---- la copia -----------------------------------------------------------------
    donor = [page[i] for i in inside]
    mapping = BM.allocate_ids(list(objects), [r.word(20) for r in donor if r.tag == 0x10003])
    new_root = mapping[DONOR_ROOT]
    profile = {'start': -1, 'pure_select': False, 'private_fields': PRIVATE_FIELDS,
               'literal_records': LITERAL_RECORDS, 'geometry_clip': -1,
               'word_writes': {ROOT_OBJECTBASE: [(36, f32(DONOR_Y), f32(ROW_Y), 'mic_row_y')]}}
    remapper = remapper_b26.Remapper(pool, mapping, set(objects), schemas, profile)
    clone = []
    for record in donor:
        data = record.data
        if record.offset == DONOR_START:            # la raíz de la fila: nombre privado
            check(record.tag == 0x10003 and record.word(20) == DONOR_ROOT, 'raíz del donante')
            data = bytearray(data)
            check(pool.resolve(BM.u32(data, 28)) == 'B2_6_2', 'nombre de la raíz del donante')
            struct.pack_into('>I', data, 28, pool.intern(ROW_NAME))
            data = bytes(data)
        clone.append((record, remapper.transform(audit.Record(record.offset, data))))
    check(not remapper.unresolved, 'el donante tiene cuerpos sin tipar: %r' % remapper.unresolved[:2])
    texts = [f['text'] for f in remapper.string_fields]
    check('MV_AUDIO_NoiseCanceller' not in texts and 'B2_6_2' not in texts and
          not {'0080', '0081', '0354'} & set(texts), 'sobrevive un vínculo original')
    check(KEPT_STRINGS <= set(texts), 'faltan cadenas compartidas esperadas en la copia')
    redirected = [c for c in remapper.changes if c['role'] == 'private_string_redirect']
    check(len(redirected) == len(PRIVATE_FIELDS), 'no se redirigieron todos los campos privados')
    literal = [c for c in remapper.changes if c['role'] == 'literal_text_not_variable_resolver']
    check(len(literal) == len(LITERAL_RECORDS), 'no todos los textos literales salieron del resolutor')
    check(sum(c['role'] == 'mic_row_y' for c in remapper.changes) == 1, 'la y de la fila no cambió')
    outside = sorted({f['old'] for f in remapper.object_fields
                      if f['scope'] == 'original_page_or_null' and f['old']})
    check(outside == [1, 37, DONOR_PARENT], 'objetos de fuera citados por la copia: %r' % outside)

    group_indexes = [i for i, r in enumerate(groups) if DONOR_START <= r.offset < DONOR_END]
    donor_clips = [audit.parse_clip(r, pool.resolve) for r in donor if r.tag == 0x1000A]
    delta = {'objects': len(donor_ids),
             'component_counts': audit.component_counts(donor, pool.resolve),
             'source_group_budget_indexes': group_indexes,
             'group_budgets': [header['groups'][i] for i in group_indexes],
             'clip_property_counts': [len(c['properties']) for c in donor_clips],
             'property_key_counts': [len(p['keys']) for c in donor_clips for p in c['properties']]}
    new_header = BM.extend_header(page[0], header, delta)

    parts, locations, cursor = [], [], 0
    for record in page:
        if record.offset == INSERT_AT:
            for original, data in clone:
                locations.append({'source_record': audit.address(original.offset), 'candidate_offset': cursor,
                                  'length': len(data), 'provenance': 'cloned_audio_submenu_row'})
                parts.append(data)
                cursor += len(data)
        if record.offset == PAGE_START:
            data = new_header
        elif record.offset == MENU_RECORD:
            data = bytearray(record.data)
            struct.pack_into('>I', data, 16, 4)
            data = bytes(data)
        else:
            data = record.data
        locations.append({'source_record': audit.address(record.offset), 'candidate_offset': cursor,
                          'length': len(data),
                          'provenance': 'stock_modified' if data != record.data else 'stock_unchanged'})
        parts.append(data)
        cursor += len(data)
    candidate = b''.join(parts)

    # ---- el resultado, releído de sus bytes ----------------------------------------
    parsed = audit.records(candidate, 0, len(candidate))
    after = audit.parse_objects(parsed, pool.resolve)
    counts = Counter(o['parent_id'] for o in after.values())
    check(len(parsed) == len(page) + len(clone) and len(after) == len(objects) + len(donor_ids),
          'recuento de la estructura')
    check(all(counts[o['id']] == o['child_count'] for o in after.values()), 'recuentos de hijos')
    check(after[new_root]['parent_id'] == DONOR_PARENT and after[DONOR_PARENT]['child_count'] == 4 and
          after[new_root]['name'] == ROW_NAME, 'la fila nueva no cuelga de Menu')
    check(set(mapping.values()).isdisjoint(objects) and max(mapping.values()) < 0x10000,
          'ids nuevos chocan con los originales o pasan de 16 bits')
    after_header = audit.parse_header(parsed[0], pool.resolve)
    check(after_header['objects'] == len(after), 'presupuesto de objetos después')
    for key in ('groups', 'clip_property_counts', 'property_key_counts', 'trailing_budget'):
        check(after_header[key][:len(header[key])] == header[key], 'cambió una reserva original: ' + key)
    for kind, count in delta['component_counts'].items():
        check(after_header['component_counts'][kind] == header['component_counts'][kind] + count,
              'presupuesto de componentes sin ampliar: ' + kind)
    check(bytes(pool.data[:pool.original_length]) == source[audit.POOL:audit.POOL_END],
          'cambió el prefijo del pool original')
    stock_changed = [l['source_record'] for l in locations if l['provenance'] == 'stock_modified']
    check(stock_changed == [audit.address(PAGE_START), audit.address(MENU_RECORD)],
          'cambio inesperado en un registro original: %r' % stock_changed)

    manifest = {
        'schema_version': 1, 'kind': 'offline_b2_6_page_candidate', 'product': 'TCAudio',
        'status': 'OFFLINE_ONLY_NOT_CAMERA_TESTED', 'camera_accessed': False,
        'source': {'seg0_sha256': audit.SEG0_SHA256, 'page': audit.address(PAGE_START),
                   'page_end_exclusive': audit.address(PAGE_END),
                   'page_sha256': digest(source[PAGE_START:PAGE_END]),
                   'nbu_offset': ENTRY_OFFSET, 'declaration': audit.address(DECLARATION),
                   'donor': 'B2_6_2 cancelador de viento (MenuItem_Toggle, hijos fijos)',
                   'donor_start': audit.address(DONOR_START), 'donor_end_exclusive': audit.address(DONOR_END),
                   'donor_sha256': digest(source[DONOR_START:DONOR_END])},
        'outputs': {NAMES[0]: {'length': len(candidate), 'sha256': digest(candidate)},
                    NAMES[1]: {'length': len(pool.data), 'sha256': digest(bytes(pool.data))}},
        'new_row': {'root_id': new_root, 'name': ROW_NAME, 'parent_id': DONOR_PARENT, 'y': ROW_Y,
                    'inserted_before': audit.address(INSERT_AT), 'mode_clip': None,
                    'title': TITLE, 'variable': VALUE_VARIABLE,
                    'values': dict((str(i), label) for i, label in enumerate(LABELS)),
                    'objects': len(donor_ids), 'records': len(clone),
                    'length': sum(len(d) for _, d in clone),
                    'object_id_map': {str(k): v for k, v in sorted(mapping.items())},
                    'stock_objects_still_referenced': outside},
        'string_pool': {'original_length': pool.original_length, 'added': pool.added},
        'budget': {'before_header_length': len(page[0].data), 'after_header_length': len(new_header),
                   'objects': after_header['objects'], 'donor_delta': delta},
        'variables_to_register': [{'name': VALUE_VARIABLE, 'type': 0, 'initial_value': 0}],
        'typed_changes': remapper.changes,
        'typed_object_fields': remapper.object_fields,
        'typed_string_fields': remapper.string_fields,
        'record_locations': locations,
        'unverified': [
            'cambio de página en tiempo de ejecución para B2_6 (Bei lo probó en MainB2 y B2_5)',
            'foco: Arriba/Abajo desde B2_6_2 llega a la tercera fila y vuelve',
            'la petición de foco submenu_01 (la de la fila de ganancia) la envía también esta fila',
            'etiquetas ASCII literales en popup y resumen (Lossless probó un título literal)',
        ],
    }
    return candidate, bytes(pool.data), manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--seg0', type=Path, default=SEG0)
    parser.add_argument('--output', type=Path, help='directorio nuevo; nunca se sobrescribe')
    parser.add_argument('--pin', action='store_true', help='imprime las huellas a fijar y sale')
    args = parser.parse_args(argv)
    try:
        audit = BM.load_audit(AUDIT)
        source = args.seg0.read_bytes()
        if args.pin:
            print('PAGE_SHA256 =', repr(digest(source[PAGE_START:PAGE_END])))
            print('DONOR_SHA256 =', repr(digest(source[DONOR_START:DONOR_END])))
            build(source, audit, pinned=False)
            return 0
        page, pool, manifest = build(source, audit)
        manifest['builder_sha256'] = digest(Path(__file__).read_bytes())
        encoded = json.dumps(manifest, indent=2, ensure_ascii=False).encode() + b'\n'
        check(args.output and not args.output.exists(), 'el directorio de salida debe ser nuevo')
        args.output.mkdir(parents=True)
        for name, data in zip(NAMES, (page, pool, encoded)):
            (args.output / name).write_bytes(data)
    except (OSError, ValueError) as exc:
        print('página B2_6 fallida: %s' % exc, file=sys.stderr)
        return 2
    if args.output:
        print(json.dumps({'output': str(args.output.resolve()), 'page_length': len(page),
                          'pool_length': len(pool), 'new_root': manifest['new_row']['root_id']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
