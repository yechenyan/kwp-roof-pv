"""Render either language of the building inventory and postcode report."""

from html import escape

from pyproj import Transformer
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from shapely.geometry import shape
from shapely.ops import transform, unary_union

from build import APP, REPORT_INPUTS, digest, read, write
from building_evidence import polygons


INK = colors.HexColor("#163c32")
MUTED = colors.HexColor("#53675d")
PALE = colors.HexColor("#edf2ec")
POSTAL = colors.HexColor("#986c38")
MARGIN = 48
WIDTH = A4[0] - 2 * MARGIN - 12


class InventoryMap(Flowable):
    def __init__(self, context):
        super().__init__()
        project = Transformer.from_crs(4326, 3035, always_xy=True)

        def project_geometry(feature):
            return transform(project.transform, shape(feature["geometry"]))

        self.boundaries = [project_geometry(f) for f in context["municipalities"]]
        self.postcodes = [project_geometry(f) for f in context["registry_by_postcode"]]
        self.points = [project_geometry(f).representative_point() for f in context["observed_pv_buildings"]]
        self.width, self.height = WIDTH, 290

    def draw(self):
        canvas = self.canv
        xmin, ymin, xmax, ymax = unary_union(self.boundaries).bounds
        scale = min((self.width - 36) / (xmax - xmin), (self.height - 36) / (ymax - ymin))
        left = (self.width - (xmax - xmin) * scale) / 2
        bottom = (self.height - (ymax - ymin) * scale) / 2

        def xy(x, y):
            return left + (x - xmin) * scale, bottom + (y - ymin) * scale

        canvas.setFillColor(PALE)
        canvas.roundRect(0, 0, self.width, self.height, 6, stroke=0, fill=1)
        for boundaries, color, width in ((self.postcodes, POSTAL, .4), (self.boundaries, MUTED, .7)):
            canvas.setStrokeColor(color)
            canvas.setLineWidth(width)
            for boundary in boundaries:
                for polygon in polygons(boundary):
                    for ring in [polygon.exterior, *polygon.interiors]:
                        path = canvas.beginPath()
                        path.moveTo(*xy(*ring.coords[0]))
                        for vertex in list(ring.coords)[1:]:
                            path.lineTo(*xy(*vertex))
                        path.close()
                        canvas.drawPath(path, stroke=1)
        canvas.setFillColor(INK)
        for point in self.points:
            canvas.circle(*xy(point.x, point.y), .65, stroke=0, fill=1)


def generate_inventory(directory, language, font):
    directory = directory.resolve()
    locale = read(APP / "locales" / f"{language}-inventory-report.json")
    inputs = {name: digest(directory / name) for name in REPORT_INPUTS}
    context = read(directory / "context.json")
    meta = context["metadata"]
    if read(directory / "summary.json") != meta:
        raise ValueError("The report context and summary belong to different builds.")
    summary = meta["summary"]
    rows = context["registry_postcode_totals"]
    candidates = summary["candidate_statuses"]
    word_wrap = "CJK" if language == "zh-CN" else None
    styles = {
        "title": ParagraphStyle("InventoryTitle", fontName=font, fontSize=26, leading=33, textColor=INK,
                                wordWrap=word_wrap, spaceAfter=11),
        "heading": ParagraphStyle("InventoryHeading", fontName=font, fontSize=14, leading=19, textColor=INK,
                                  wordWrap=word_wrap, spaceBefore=12, spaceAfter=7),
        "body": ParagraphStyle("InventoryBody", fontName=font, fontSize=10, leading=15, textColor=INK,
                               wordWrap=word_wrap, spaceAfter=9),
        "caption": ParagraphStyle("InventoryCaption", fontName=font, fontSize=8, leading=12, textColor=MUTED,
                                  wordWrap=word_wrap, spaceAfter=8),
        "cell": ParagraphStyle("InventoryCell", fontName=font, fontSize=9, leading=13, textColor=INK,
                               wordWrap=word_wrap),
        "head": ParagraphStyle("InventoryTableHeader", fontName=font, fontSize=9, leading=13,
                               textColor=colors.white, wordWrap=word_wrap),
    }

    def p(text, style="body"):
        return Paragraph(escape(str(text)), styles[style])

    def table(headers, values, fractions):
        contents = [[p(header, "head") for header in headers], *[[p(cell, "cell") for cell in row] for row in values]]
        return Table(contents, colWidths=[WIDTH * width for width in fractions], repeatRows=1,
                     style=TableStyle([("BACKGROUND", (0, 0), (-1, 0), INK),
                                       ("ROWBACKGROUNDS", (0, 1), (-1, -1), [PALE, colors.white]),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                       ("TOPPADDING", (0, 0), (-1, -1), 7),
                                       ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))

    metrics = [[label, f"{summary[key]:,.0f}" if key != "projected_pv_area_m2" else f"{summary[key]:,.2f}"]
               for key, label in locale["metrics"].items()]
    postal_rows = [[r["municipality"], r["postcode"] or locale["missing_postcode"], f"{r['target_count']:,}",
                    f"{r['target_power_kw']:,.3f}", f"{r['post_capture_count']:,}"] for r in rows]
    postal_rows.append([locale["total"], "", f"{sum(r['target_count'] for r in rows):,}",
                        f"{sum(r['target_power_kw'] for r in rows):,.3f}",
                        f"{sum(r['post_capture_count'] for r in rows):,}"])
    registry_note = locale["registry_scope"].format(records=summary["registry_records"], zones=len(rows),
                                                    snapshot=meta["registry_snapshot_date"])
    candidate_note = locale["candidate_coverage"].format(
        listed=candidates.get("ranked", 0) + candidates.get("low_compatibility", 0),
        ranked=candidates.get("ranked", 0), low=candidates.get("low_compatibility", 0),
        eligible=summary["strict_under30_targets"] - sum(r["post_capture_count"] for r in rows),
        minimum=f'{summary["candidate_threshold"]:g}',
        verified=summary["verified_identities"],
    )
    story = [p(locale["title"], "title"), p(locale["area"], "heading"),
             p(locale["capture"].format(dates=" / ".join(meta["capture_dates"]))),
             table(locale["metric_headers"], metrics, [.73, .27]), Spacer(1, 13),
             InventoryMap(context), Spacer(1, 6), p(locale["map_caption"], "caption"),
             p(locale["geometry"], "heading"), p(locale["grouping"]), p(locale["lod2"]),
             PageBreak(), p(locale["registry_title"], "title"), p(registry_note),
             table(locale["postal_headers"], postal_rows, [.28, .15, .16, .19, .22]),
             p(locale["candidates"], "heading"), p(candidate_note),
             p(locale["joint_proposals"].format(proposals=summary["joint_proposal_count"],
                                                coordinates=summary["states"].get("inferred_image_association", 0))),
             p(locale["postal_method"]),
             p(locale["gis_title"], "heading"), p(locale["gis"]), p(locale["sources"], "caption")]
    path = directory / ("roof-pv-report-zh.pdf" if language == "zh-CN" else "roof-pv-report.pdf")
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=46, bottomMargin=45, title=locale["title"])

    def decorate(canvas, document):
        canvas.setFont(font, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, A4[1] - 28, locale["running_header"])
        canvas.drawString(MARGIN, 26, meta["algorithm"])
        canvas.drawRightString(A4[0] - MARGIN, 26, str(document.page))

    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    if any(digest(directory / name) != checksum for name, checksum in inputs.items()):
        raise ValueError("The report inputs changed during rendering.")
    write(path.with_suffix(".provenance.json"), {"inputs_sha256": inputs, "pdf_sha256": digest(path)})
    print(f"Created {path.name}.")
