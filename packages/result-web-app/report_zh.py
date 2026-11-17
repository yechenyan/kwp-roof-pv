"""Render the Chinese report from a separate localization resource."""
from collections import Counter
from datetime import datetime
from html import escape
import os
from pathlib import Path
import statistics
import subprocess
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, PageBreak

from build import APP, read
from report import GREEN, MUTED, PALE, WIDTH, SurveyMap


def register_font():
    configured = os.environ.get("ROOF_PV_REPORT_FONT")
    candidates = [Path(configured)] if configured else []
    if not configured:
        for family in ["Arial Unicode MS", "Noto Sans SC", "WenQuanYi Zen Hei"]:
            try:
                result = subprocess.run(["fc-match", "-f", "%{file}", family],
                                        capture_output=True, text=True, check=True)
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
            candidates.append(Path(result.stdout.strip()))
    for path in candidates:
        if path.is_file() and path.suffix.lower() == ".ttf":
            try:
                pdfmetrics.registerFont(TTFont("ReportCJK", str(path)))
                return "ReportCJK"
            except Exception:
                continue
    raise RuntimeError("An embeddable Chinese TrueType font is required. Set ROOF_PV_REPORT_FONT to a font resolved at runtime.")


def generate(directory):
    resource = read(APP / "locales" / "zh-CN-report.json")
    metadata = read(directory / "summary.json")
    summary = metadata["summary"]
    records = read(directory / "records.json")
    context = read(directory / "context.json")
    models = read(directory / "model-comparison.json")
    targets = [r for r in records if r["cohort"] == "active_building_under30"]
    extra_targets = [r for r in records if r["cohort"] == "active_building_exactly30"]
    states = Counter(r["association_status"] for r in targets)
    values = {key: f"{value:,}" if isinstance(value, int) else value for key, value in summary.items()
              if not isinstance(value, dict)}
    values.update(date=datetime.fromisoformat(metadata["created_at"]).astimezone(ZoneInfo("Europe/Berlin")).date().isoformat(),
                  algorithm=metadata["algorithm"], registry_date=metadata["registry_snapshot_date"],
                  target_image=f"{states['inferred_image_association']:,}",
                  target_statistical=f"{states['statistical_building_allocation']:,}",
                  target_unlocated=f"{sum(not r['point'] for r in targets):,}",
                  image_links=f"{summary['states'].get('inferred_image_association', 0):,}",
                  statistical_links=f"{summary['states'].get('statistical_building_allocation', 0):,}",
                  confidence_low=f"{summary['confidence'].get('low', 0):,}",
                  confidence_medium=f"{summary['confidence'].get('medium', 0):,}",
                  shared_location_diameter=f"{summary['maximum_shared_location_diameter_m']:,.1f}",
                  storage_power=f"{summary['unique_storage_power_kw']:,.3f}",
                  storage_energy=f"{summary['unique_storage_capacity_kwh']:,.3f}",
                  snapshot_id=metadata["registry_snapshot"], run_id=metadata["run_id"],
                  gpkg_sha=read(directory / "verification.json")["gpkg_sha256"])
    labels = resource["labels"]
    overview = [[label, str(values[key])] for label, key in resource["overview_rows"]]
    outcomes = []
    for name in ["Hohen Neuendorf", "Birkenwerder"]:
        cohort = [r for r in targets if r["municipality"] == name]
        count = Counter(r["association_status"] for r in cohort)
        outcomes.append([name, len(cohort), count["inferred_image_association"], count["statistical_building_allocation"], sum(not r["point"] for r in cohort)])
    outcomes.append([labels["total"], len(targets), states["inferred_image_association"], states["statistical_building_allocation"], sum(not r["point"] for r in targets)])
    model_rows = []
    model_labels = resource["model_labels"]
    for entrant in models["public"]:
        if not entrant["eligible"]:
            continue
        metrics = entrant["objects_50"] or {}
        pixels = entrant.get("pixels") or {}
        number = lambda value: labels["not_available"] if value is None else f"{value:.3f}"
        model_rows.append([model_labels.get(entrant["id"], entrant["id"]),
                           number(metrics.get("precision")), number(metrics.get("recall")),
                           number(metrics.get("f1")), number(pixels.get("iou"))])
    pilot_rows = [[r["model"] + " / " + r["band"], f"{r['matched_reference_regions']} / 12", r["unmatched_detections"]]
                  for r in (models.get("pilot") or {}).get("runs", [])]
    storage_rows = [[label, str(values[key])] for label, key in resource["storage_rows"]]
    location_rows = [[labels["shared_locations"], values["shared_located_locations"]],
                     [labels["over_limit"], str(summary["shared_locations_over_limit"])],
                     [labels["max_diameter"], values["shared_location_diameter"]]]
    data = {"overview": overview, "outcomes": outcomes, "models": model_rows, "pilot": pilot_rows,
            "storage": storage_rows, "location_consistency": location_rows}
    font = register_font()
    styles = {
        "title": ParagraphStyle("CJKTitle", fontName=font, fontSize=26, leading=35, textColor=GREEN, wordWrap="CJK", spaceAfter=17),
        "heading": ParagraphStyle("CJKHeading", fontName=font, fontSize=18, leading=25, textColor=GREEN, wordWrap="CJK", spaceAfter=15),
        "section": ParagraphStyle("CJKSection", fontName=font, fontSize=11, leading=17, textColor=GREEN, wordWrap="CJK", spaceBefore=8, spaceAfter=6),
        "body": ParagraphStyle("CJKBody", fontName=font, fontSize=9.5, leading=15, textColor=GREEN, wordWrap="CJK", spaceAfter=9),
        "small": ParagraphStyle("CJKSmall", fontName=font, fontSize=8, leading=12, textColor=MUTED, wordWrap="CJK", spaceAfter=7),
        "cell": ParagraphStyle("CJKCell", fontName=font, fontSize=8, leading=12, textColor=GREEN, wordWrap="CJK"),
        "head": ParagraphStyle("CJKHead", fontName=font, fontSize=8, leading=12, textColor=colors.white, wordWrap="CJK"),
    }
    def paragraph(text, style="body"):
        return Paragraph(escape(str(text).format_map(values)).replace("\n", "<br/>"), styles[style])
    story = []
    for page_index, page in enumerate(resource["pages"]):
        if page_index:
            story.append(PageBreak())
        story.append(paragraph(resource["page_label"].format(number=page_index + 1), "small"))
        story.append(paragraph(page["title"], "title" if page_index == 0 else "heading"))
        for block in page["blocks"]:
            kind = block["kind"]
            if kind in {"body", "small", "section"}:
                story.append(paragraph(block["text"], kind))
            elif kind == "map":
                story.extend([SurveyMap(targets, context["municipalities"], height=245), Spacer(1, 9)])
            elif kind == "table":
                rows = data[block["data"]] if "data" in block else block["rows"]
                table_data = [[paragraph(v, "head") for v in block["headers"]]]
                table_data += [[paragraph(v, "cell") for v in row] for row in rows]
                weights = block.get("widths", [1] * len(block["headers"]))
                widths = [WIDTH * w / sum(weights) for w in weights]
                table = Table(table_data, colWidths=widths, repeatRows=1, hAlign="LEFT")
                table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREEN),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LINEBELOW", (0, 0), (-1, -1), .3, colors.HexColor("#dbe0d4"))]))
                story.extend([table, Spacer(1, 10)])
    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#d8dfd3"))
        canvas.line(48, A4[1] - 35, A4[0] - 48, A4[1] - 35)
        canvas.setFillColor(GREEN)
        canvas.setFont(font, 7)
        canvas.drawString(48, A4[1] - 25, resource["running_header"])
        canvas.setFillColor(MUTED)
        canvas.drawString(48, 27, resource["running_footer"].format_map(values))
        canvas.drawRightString(A4[0] - 48, 27, str(doc.page))
        canvas.restoreState()
    path = directory / "roof-pv-report-zh.pdf"
    document = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=48, rightMargin=48,
        topMargin=55, bottomMargin=48, title=resource["document_title"], author="",
        subject=resource["document_subject"])
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    print(f"Created {path.name}")


if __name__ == "__main__":
    generate(APP / "dist")
