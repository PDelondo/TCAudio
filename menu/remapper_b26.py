"""Remapper de Bei (fpSup/lossless/menu/build_menu_candidate.py) con una sola
diferencia: la lista de propiedades animadas admitidas incluye border-width (float), que la
fila del viento (B2_6_2) anima, y do-fill (booleano). Son valores, no referencias a un
objeto ni a una cadena, así que no hay nada que reasignar en sus claves."""
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ['FPSUP_REPO']) / 'lossless/menu'))
import build_menu_candidate as BM  # noqa: E402
from build_menu_candidate import check, u32  # noqa: E402,F401

ANIMATED = {"is-visible", "position", "color", "rect", "size", "text", "type", "border-width", "do-fill"}


class Remapper(BM.Remapper):
    def clip(self, data, source):
        cursor = 28
        for _ in range(u32(data, 12)):
            count, kind, component, name, dimension = struct.unpack_from(">5I", data, cursor)
            check(kind in (0, 1, 2), "unsupported animation type")
            self.string_field(data, source, cursor + 12, "animated_property_name")
            property_name = self.pool.resolve(name)
            check(property_name in ANIMATED,
                  "unclassified animated property: " + property_name)
            for index in range(count):
                key = cursor + 20 + 10 * index
                check(key + 10 <= len(data), "truncated animation key")
                check(data[key] <= 1 and data[key + 1] <= 1, "unhandled tangent key")
                if kind == 2:
                    replacements = self.profile.get("text_animation_values", {}).get(source)
                    if replacements is not None and property_name == "text":
                        old_text = self.pool.resolve(u32(data, key + 6))
                        check(old_text in replacements, "text animation source label mismatch")
                        self.write(data, source, key + 6, self.pool.intern(replacements[old_text]), "literal_text_animation")
                    self.string_field(data, source, key + 6, "animation_string_value")
                if source == self.profile["geometry_clip"] and not self.profile["pure_select"] and property_name == "position" and dimension == 1 and u32(data, key + 2) == 66:
                    check(kind == 1 and u32(data, key + 6) == 0x43220000, "donor CINE y mismatch")
                    self.write(data, source, key + 6, 0x43730000, "cine_row_y_162_to_243")
                if source == self.profile["geometry_clip"] and self.profile["pure_select"] and property_name == "is-visible":
                    time, old = u32(data, key + 2), u32(data, key + 6)
                    check((time, old) in ((0, 1), (66, 0)), "pure donor visibility source mismatch")
                    self.write(data, source, key + 6, 1 - old, "still_hidden_cine_visible")
            cursor += 20 + count * 10
        check(cursor == len(data), "unconsumed clip bytes")
