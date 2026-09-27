"""Download and cache the Docling models used by the pipeline (run at image build time).

Converting the bundled demo PDF with the same pipeline options guarantees exactly the
models the app needs are in the Hugging Face cache, so containers never download at runtime.
"""

from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

opts = PdfPipelineOptions()
opts.do_ocr = False
opts.do_table_structure = True
converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
result = converter.convert(str(Path(__file__).resolve().parents[1] / "app" / "demo" / "demo_statement.pdf"))
print(f"docling warm-up ok: {len(result.document.tables)} tables")
