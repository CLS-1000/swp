from __future__ import annotations


def make_pdf(pages: list[str]) -> bytes:
    """Minimal text PDF (Helvetica) so tests need no PDF writer dependency."""
    objs: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font_id = 3 + 2 * n
    for i, text in enumerate(pages):
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode()
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


MD_DOC = """# Synthetic SOP

Intro text for the synthetic fixture.

## Starter circuit

Check the synthetic widget supply at the connector. Synthetic value here is fictional.

## CAN bus

Terminate the synthetic bus with a synthetic resistor.
"""

SPEC_CSV = """make,model,years,ecu_family,quantity,value,min,max,unit,measurement_state,page
Synthetic,Widget,2001-2005,Synthmarelli,ECU supply,,1.11,2.22,V,KOEO,7
Synthetic,Widget,2001-2005,Synthmarelli,Battery floor,3.33,,,V,CRANKING,8
,,,Synthmarelli,Bad row,,,,V,KOEO,9
"""


def build_docs(root) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manual.pdf").write_bytes(make_pdf(["Synthetic manual page one starter relay", "Synthetic manual page two ground strap"]))
    (root / "sop.md").write_text(MD_DOC)
    (root / "specs.csv").write_text(SPEC_CSV)
    (root / "notes.txt").write_text("Plain note about synthetic crankshaft sensor.")
    (root / "image.png").write_bytes(b"\x89PNG")


class StubLLM:
    """Scripted stand-in for the model. `explain_fn(system, user)` and `extract_fn(text)` are plain callables."""

    def __init__(self, explain_fn=None, extract_fn=None):
        self.explain_fn = explain_fn or (lambda system, user: "Take the reading at the spot described. What's the reading?")
        self.extract_fn = extract_fn or (lambda text: {})
        self.explain_calls = []
        self.extract_calls = []

    def explain(self, system, user):
        self.explain_calls.append((system, user))
        return self.explain_fn(system, user)

    def extract(self, text):
        self.extract_calls.append(text)
        return self.extract_fn(text)
