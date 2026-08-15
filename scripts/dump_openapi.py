"""OpenAPI semasini sunucu acmadan dosyaya doker.

`web/package.json` icindeki `gen:types` bunu kullaniyor. Onceki hali
`openapi-typescript http://localhost:8000/openapi.json` idi; calisan bir
uvicorn'a bagimli oldugu icin CI'da ekstra tesisat gerektiriyordu (sunucuyu
baslat, hazir olmasini bekle, kapat) ve bu yuzden hic kosulmuyordu.
`app.openapi()` ayni semayi surec icinde uretiyor.

Cikti yolu depo kokune gore sabit; hangi dizinden cagrildigi onemli degil.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "web" / "openapi.json"


def _add_sse_bodies(schema: dict) -> None:
    """SSE olay govdelerini OpenAPI bilesenlerine ekler.

    FastAPI yalnizca ROTA yanitlarindan sema uretiyor. `/events` ucu bir
    `StreamingResponse` donduruyor, dolayisiyla akan olaylarin govdeleri
    OpenAPI'de hic gorunmuyor -- ve sozlesmenin en cok kaydigi yer tam da orasi,
    cunku o govdeler API katmanindan uzakta, is katmaninda uretiliyor.
    Buraya acikca eklenmezlerse tip uretimi onlari kapsamaz.
    """
    from api.schemas import RunSnapshotBody
    from src.models import ProgressEvent

    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for model in (ProgressEvent, RunSnapshotBody):
        components[model.__name__] = model.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )


def _collect_refs(node: object, schemas: dict, seen: set[str]) -> None:
    """`node` altindan ulasilabilen tum sema adlarini `seen`e toplar."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[1]
            if name not in seen:
                seen.add(name)
                _collect_refs(schemas.get(name, {}), schemas, seen)
        for value in node.values():
            _collect_refs(value, schemas, seen)
    elif isinstance(node, list):
        for value in node:
            _collect_refs(value, schemas, seen)


def _mark_response_fields_required(schema: dict) -> list[str]:
    """Yanit semalarindaki alanlari zorunlu isaretler.

    Neden gerekli: pydantic'te varsayilani olan bir alan JSON Schema'da
    "required" sayilmiyor. Bu ISTEK tarafi icin dogru (istemci atlayabilir) ama
    YANIT tarafi icin yaniltici: FastAPI yaniti serilestirirken varsayilanlari
    da yaziyor, yani anahtar tel uzerinde HER ZAMAN var. Olcerek dogrulandi --
    `/api/config` yaniti `defaults` anahtarini sema "opsiyonel" dedigi halde
    iceriyor.

    Isaretlenmezse uretilen TypeScript gereksiz yere kotumser olur (`warnings`
    icin `?? []` gibi hicbir zaman calismayacak dallar) ve gercek kaymalar bu
    gurultunun icinde kaybolur. `T | None` alanlar zaten nullable kaliyor:
    "zorunlu ama null olabilir" tel uzerindeki durumu tam olarak anlatiyor.

    Hangi semanin istek tarafi oldugu ELLE listelenmiyor, dokumandan cikariliyor:
    bir `requestBody`den ulasilabilen her sema dokunulmadan birakiliyor. Boylece
    ileride eklenen bir istek modeli sessizce zorunlu isaretlenmez.
    """
    schemas = schema.get("components", {}).get("schemas", {})
    request_side: set[str] = set()
    for path in schema.get("paths", {}).values():
        for operation in path.values():
            if isinstance(operation, dict) and "requestBody" in operation:
                _collect_refs(operation["requestBody"], schemas, request_side)

    touched = []
    for name, subschema in sorted(schemas.items()):
        if name in request_side:
            continue
        properties = subschema.get("properties")
        if not properties:
            continue
        already = set(subschema.get("required", []))
        missing = [p for p in properties if p not in already]
        if missing:
            subschema["required"] = sorted(properties)
            touched.append(f"{name}(+{len(missing)})")
    return touched


def main(argv: list[str]) -> int:
    # Depo kokunu import yoluna ekle: script `web/` icinden de cagriliyor.
    sys.path.insert(0, str(ROOT))

    from api.main import app

    output = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)

    schema = app.openapi()
    _add_sse_bodies(schema)
    touched = _mark_response_fields_required(schema)
    # `sort_keys` + sabit girinti: cikti deterministik olmali, yoksa CI'daki
    # "yeniden uret ve diff al" kontrolu anlamsiz farklarla kirmizi yanar.
    output.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    count = len((schema.get("components") or {}).get("schemas", {}))
    print(f"{output.relative_to(ROOT)} yazildi ({count} sema)")
    if touched:
        print(f"  yanit alanlari zorunlu isaretlendi: {', '.join(touched)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
