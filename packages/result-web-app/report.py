"""Produce a paginated, reproducible English PDF from the delivered snapshot."""
from collections import Counter, defaultdict
from pathlib import Path
from html import escape
import json
import math
import statistics

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable, KeepTogether
from reportlab.pdfbase.pdfmetrics import stringWidth
from build import APP, read, write, digest

GREEN=colors.HexColor("#173f32")
MUTED=colors.HexColor("#66796e")
PALE=colors.HexColor("#eff2e8")
ORANGE=colors.HexColor("#d88533")
WIDTH=A4[0]-96


class SurveyMap(Flowable):
    def __init__(self,records,municipalities,width=WIDTH,height=270):
        Flowable.__init__(self)
        self.records=records
        self.municipalities=municipalities
        self.width=width
        self.height=height

    def draw(self):
        c=self.canv
        coords=[]
        def rings(geometry):
            return geometry["coordinates"] if geometry["type"]=="Polygon" else [r for polygon in geometry["coordinates"] for r in polygon]
        for f in self.municipalities:
            for ring in rings(f["geometry"]): coords.extend(ring)
        x0=min(p[0] for p in coords); x1=max(p[0] for p in coords)
        y0=min(p[1] for p in coords); y1=max(p[1] for p in coords)
        # Scale longitude by local latitude so geographic aspect remains meaningful.
        factor=math.cos(math.radians((y0+y1)/2))
        scale=min((self.width-30)/((x1-x0)*factor),(self.height-30)/(y1-y0))
        def xy(p): return 15+(p[0]-x0)*factor*scale,15+(p[1]-y0)*scale
        c.setFillColor(PALE); c.roundRect(0,0,self.width,self.height,6,fill=1,stroke=0)
        for f in self.municipalities:
            for ring in rings(f["geometry"]):
                path=c.beginPath();path.moveTo(*xy(ring[0]))
                for p in ring[1:]:path.lineTo(*xy(p))
                path.close();c.setStrokeColor(MUTED);c.setLineWidth(.6);c.drawPath(path)
        for r in self.records:
            if not r["point"]:continue
            c.setFillColor(ORANGE if r["association_status"]=="statistical_building_allocation" else GREEN)
            c.circle(*xy(r["point"]),1.1,fill=1,stroke=0)
        c.setFont("Helvetica-Bold",9);c.setFillColor(GREEN);c.drawRightString(self.width-12,self.height-18,"N ↑".replace("↑",""))
        c.setFont("Helvetica",7);c.drawRightString(self.width-12,12,"Geographic overview; no basemap; inferred locations")


def generate(directory):
    metadata=read(directory/"summary.json");s=metadata["summary"]
    records=read(directory/"records.json");context=read(directory/"context.json")
    comparison=read(directory/"model-comparison.json")
    verification=read(directory/"verification.json")
    targets=[r for r in records if r["cohort"] == "active_building_under30"]
    target_states=Counter(r["association_status"] for r in targets)
    associated=[r for r in records if r["association_status"]=="inferred_image_association"]
    scores=[r["score"] for r in associated]
    modified=sum(r.get("group_id")!=r.get("unconstrained_top_group_id") for r in associated)
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitlePV",fontName="Helvetica-Bold",fontSize=29,leading=34,textColor=GREEN,spaceAfter=18))
    styles.add(ParagraphStyle(name="SubPV",fontSize=15,leading=21,textColor=MUTED,spaceAfter=15))
    styles.add(ParagraphStyle(name="BodyPV",fontSize=10,leading=15,spaceAfter=9,textColor=GREEN))
    styles.add(ParagraphStyle(name="SmallPV",fontSize=8,leading=11,spaceAfter=7,textColor=MUTED))
    styles.add(ParagraphStyle(name="HeadingPV",fontSize=18,leading=23,spaceAfter=15,textColor=GREEN))
    styles.add(ParagraphStyle(name="SectionPV",fontSize=11,leading=15,spaceBefore=9,spaceAfter=7,textColor=GREEN,fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="TablePV",fontSize=8,leading=11,textColor=GREEN))
    styles.add(ParagraphStyle(name="TableHeadPV",fontSize=8,leading=11,textColor=colors.white,fontName="Helvetica-Bold"))
    story=[]
    def p(text,style="BodyPV"):
        return Paragraph(text,styles[style])
    def add(text,style="BodyPV"): story.append(p(text,style))
    def heading(number,title):
        add(f"{number:02d} / PROJECT REPORT","SmallPV");add(title,"HeadingPV")
    def table(headers,rows,widths=None):
        content=[[p(escape(str(v)),"TableHeadPV") for v in headers]]+[[p(escape(str(v)),"TablePV") for v in row] for row in rows]
        t=Table(content,colWidths=widths or [WIDTH/len(headers)]*len(headers),repeatRows=1,hAlign="LEFT")
        t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),GREEN),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,PALE]),
                              ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),
                              ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
                              ("LINEBELOW",(0,0),(-1,-1),.3,colors.HexColor("#dbe0d4"))]))
        story.extend([t,Spacer(1,12)])
    def page():story.append(PageBreak())

    add("KWP / HOHEN NEUENDORF + BIRKENWERDER","SmallPV")
    story.append(Spacer(1,20))
    add("Rooftop solar.<br/>Registry associations.<br/>Explicit uncertainty.","TitlePV")
    add("Municipal rooftop-PV geolocation and technical methodology","SubPV")
    add(f"17 November 2026 | MaStR snapshot {metadata['registry_snapshot_date']} | {metadata['algorithm']}","SmallPV")
    table(["Result", "Delivered"],[
        ["Solar registry records retained",f"{s['registry_records']:,}"],
        ["Image regions / proposed array groups",f"{s['image_candidates']:,} / {s['array_groups']:,}"],
        ["Selected image associations",f"{s['states'].get('inferred_image_association',0):,}"],
        ["Post-capture statistical building allocations",f"{s['states'].get('statistical_building_allocation',0):,}"],
        ["Independently verified installation identities", "0"],
        ["Spatial reference of GeoPackage layers", "EPSG:3035"]],[WIDTH*.7,WIDTH*.3])
    add("Project objectives and outputs","SectionPV")
    add("A reproducible, highest-feasible-score association result, a separate statistical allocation for later records, an interactive WMS result atlas, and technical GIS exports with storage relationships. Confidence and reasons can be edited without changing the original image review.")
    add("How to use it","SectionPV")
    add("Use the result as an uncertainty-labelled planning inventory. An automatic association is not proof that a MaStR installation occupies the selected building. Engineering scores are not probabilities; independent small-system identity accuracy remains unmeasured.")
    add("Technical-only delivery: operator identifiers, personal names, street addresses and original XML fragments are excluded. Public installation and equipment identifiers are retained for traceability.","SmallPV")
    page()

    heading(2,"Data foundation and temporal scope")
    table(["Input", "Frozen scope / interpretation"],[
        ["MaStR", "Official nationwide export dated 2026-11-15, filtered by municipality codes 12065144 and 12065036. 3,476 solar records, 1,971 storage units and 1,971 storage systems."],
        ["DOP20 RGBI", "99 original four-band 20 cm source tiles; captures 2026-04-22 and 2026-05-01. RGB and RGBI models use the same frozen raster source."],
        ["Study boundary", "Official Hohen Neuendorf / Birkenwerder union, approximately 66.65 km2; original inference reports no missing target pixels."],
        ["ALKIS context", "25,813 original building/construction footprints intersecting the study area. Current cadastral geometry is not asserted contemporaneous with imagery."],
        ["Image candidates", "6,355 segmentation review regions, grouped into 5,038 proposed arrays. 3,702 groups satisfy the result pipeline's building-support screen."],
        ["Live WMS", "RGB layer bebb_dop20c; CIR layer bb_dop20cir. Both services verified with GetCapabilities and nonblank GetMap requests. Live imagery is contextual, not substituted into frozen scores."]],[WIDTH*.24,WIDTH*.76])
    add("Registry cohorts","SectionPV")
    add("The principal target is active building solar, positive gross power strictly below 30 kW and no public coordinate pair: 2,318 records. The two exactly-30-kW records are retained as a separate additional cohort. Larger, inactive, balcony and other records remain visible as context; they are not silently dropped.")
    add("Capture date, commissioning date and publication date differ","SectionPV")
    add("Temporal compatibility uses actual capture dates associated with array geometry. Commissioning can follow physical panel installation. A later commissioning date therefore signals uncertainty rather than proven absence. Records commissioned after the latest capture date enter the separate statistical building allocator.")
    add("Provenance","SectionPV")
    add("Registry and source-batch hashes are checked before building results. Candidate, municipality and building files are checked against the immutable run manifest. A changed source review revision requires rebuilding the grouping batch; original review data is opened read-only.")
    page()

    heading(3,"Scoring and association policy")
    add("Continuous area compatibility","SectionPV")
    add("For observed horizontal area A and expected projected area E, similarity is exp[-0.5 × (ln(A/E) / 0.55)<super>2</super>]. The function expresses continuous compatibility within broad plausible area ranges. This numerical distinction does not establish identity.")
    table(["Commissioning era", "Module area prior (m2)", "Power density (kW/m2)"],[
        ["Before 2005","1.30","0.120"],["2005-2014","1.60","0.150"],["2015-2019","1.75","0.175"],
        ["2020-2022","1.95","0.195"],["2023 onward","2.05","0.210"]],[WIDTH*.4,WIDTH*.3,WIDTH*.3])
    add("Expected module area = module count × era module area × cosine(tilt). Expected power area = gross power / era power density × cosine(tilt). Tilt uses the midpoint of the registered category; main and secondary category cosines are averaged when present. Missing categories use 15-45 degrees; cosine is floored at 0.25. These are engineering priors, not roof measurements.","SmallPV")
    add("Score composition","SectionPV")
    add("Area score = 0.65 × module similarity + 0.35 × power similarity. Suspect module counts are omitted; a single available area feature receives full weight. These correlated features are not independent evidence. Image quality = 0.55 × building overlap + 0.45 × fraction of members with RGB/RGBI agreement. Base score = 100 × (0.85 × area score + 0.15 × image quality).")
    add("A valid public coordinate uses 80% exponential distance compatibility exp(-distance/25 m) and 20% base score, within 100 m. Coordinates outside the declared municipality are excluded. Commissioning after a candidate's latest capture multiplies its score by 0.7. Facing azimuth, exact roof slope and postcode geometry are not measured or scored.","SmallPV")
    add("Highest feasible score, with disclosed constraints","SectionPV")
    add("Allocate coordinate-bearing records first, then descending power, then solar ID. Choose the highest-scoring available building-supported group (mean overlap at least 50%). Each image group admits one registry location; multiple units at that same location may share it. Combined power may not exceed 0.5 kW per observed m2. A minimum compatibility score of 30/100 screens poor fits. Unavailable or weak outcomes remain unlocated.")
    add("All selected points at one registry location must also lie within 100 m of every other selected point at that location, across image and statistical stages. This pairwise rule prevents chain drift. The first inferred position anchors the cluster; missing location IDs use separate solar IDs. No compatible local option means unlocated, not a distant substitution. This greedy rule is not a global optimum or verified identity.","SmallPV")
    page()

    heading(4,"Geolocation results and spatial coverage")
    table(["Scope", "Image links", "Statistical", "Unlocated / other"],[
        [name,
         sum(r["municipality"]==name and r["association_status"]=="inferred_image_association" for r in targets),
         sum(r["municipality"]==name and r["association_status"]=="statistical_building_allocation" for r in targets),
         sum(r["municipality"]==name and not r["point"] for r in targets)]
        for name in ["Hohen Neuendorf","Birkenwerder"]]+[["Anonymous targets <30 kW",target_states["inferred_image_association"],target_states["statistical_building_allocation"],sum(not r["point"] for r in targets)]],
        [WIDTH*.43,WIDTH*.19,WIDTH*.19,WIDTH*.19])
    story.append(SurveyMap(targets,context["municipalities"]))
    story.append(Spacer(1,10))
    add("Green: inferred image association. Orange: statistical building allocation. The map displays the selected result; it does not express validated positional accuracy.","SmallPV")
    add(f"Across all cohorts, {s['located_records']:,} records have a selected result point. Median image compatibility is {statistics.median(scores):.1f}/100. {modified:,} image associations differ from the unconstrained top group because of allocation constraints. {s['competing_groups']} groups are shared by registrations at the same registry location; the broad capacity-conflict screen reports {s['capacity_conflict_records']} conflicts.")
    add("A zero conflict count means compliance with this allocation policy, not correctness of identities. Coverage is a result-production statistic, not recall. Small array fragments and incomplete grouping can force genuine installations to remain unlocated or choose a weaker feasible alternative.","SmallPV")
    add(f"Shared-location consistency: {s['shared_located_locations']} registry locations have multiple selected points; their maximum diameter is {s['maximum_shared_location_diameter_m']:.1f} m. No selected location exceeds the 100 m rule. This is a constraint check, not positional accuracy.","SmallPV")
    page()

    heading(5,"Model comparison and selection evidence")
    add("Public model benchmark","SectionPV")
    add("The public model benchmark evaluates eight 800 × 800 crops with published human-corrected masks: four positive-targeted and four reference-empty convenience crops. Object F1 uses box IoU ≥0.5; mask IoU is micro-aggregated. The sample is a domain proxy dominated by larger systems, not a representative under-30-kW municipal validation.")
    rows=[]
    labels={"a1_deeplab":"DeepLabV3 / RGB","a2_segformer":"SegFormer / RGB","a3_brandenburg":"Brandenburg / RGB","a4_yolo":"YOLOv8s-seg / RGB",
            "a5_maskrcnn":"Mask R-CNN / RGB","a7_gpt_sol":"GPT Sol / RGB boxes","a8_gpt_terra":"GPT Terra / RGB boxes","a3_brandenburg_rgbi":"Brandenburg / RGBI"}
    for e in comparison["public"]:
        if not e["eligible"]:continue
        m=e["objects_50"] or {};pixels=e.get("pixels") or {}
        fmt=lambda v:"n/a" if v is None else f"{v:.3f}"
        rows.append([labels.get(e["id"],e["id"]),fmt(m.get("precision")),fmt(m.get("recall")),fmt(m.get("f1")),fmt(pixels.get("iou"))])
    table(["Entrant / input","Precision","Recall","Object F1","Mask IoU"],rows,[WIDTH*.4,WIDTH*.15,WIDTH*.15,WIDTH*.15,WIDTH*.15])
    add("The Brandenburg RGB segmenter has mask IoU 0.896 on this fixed sample; its RGBI counterpart has 0.874. GPT Sol has the highest RGB box F1 (0.842) but no pixel mask. SAM 3 is ineligible because complete predictions were unavailable. These results support using a reproducible specialist segmenter for full-area outlines; they do not prove RGBI superiority or local identity accuracy.","SmallPV")
    add("Local sample-area model experiment","SectionPV")
    pilot=comparison.get("pilot")
    if pilot:
        add("The pilot covers eight 160 m × 160 m scenes (0.2048 km2), 32 paired tiles and 12 provisional visual reference regions. Its references are neither exhaustive nor independent human ground truth.")
        table(["Pilot track","Reference hits","Unmatched detections"],[[r["model"]+" / "+r["band"],f"{r['matched_reference_regions']} / 12",r["unmatched_detections"]] for r in pilot["runs"]],[WIDTH*.5,WIDTH*.25,WIDTH*.25])
        add("Unmatched detections are not established false positives; hit ratios are not validated recall. The CIR pilot passed false-colour inputs to RGB-trained weights, a distribution-shift experiment rather than a clean NIR ablation. The full municipal pipeline instead retains dedicated RGB and four-band RGBI models.","SmallPV")
    page()

    heading(6,"Later installations and storage relationships")
    add("Post-capture fallback: a separate planning product","SectionPV")
    add(f"{s['states'].get('statistical_building_allocation',0)} later records receive statistical building allocations. Candidate buildings have area 30-1,000 m2. Unanchored sites generally use buildings without detected PV or earlier allocation. An anchored registry location may reuse its associated building within the pairwise 100 m cluster limit, with detected PV area and earlier allocation demand deducted from 45% of footprint area. Insufficient local capacity leaves a record unlocated.")
    add("Score = 100 × (0.8 × roof-size suitability + 0.2 × compactness). Size suitability compares remaining usable area to 1.4 × expected demand (log-Gaussian sigma 0.6); compactness is min(1, 4π × area / perimeter²). Records use descending power / ID order. Stable building IDs break ties. Footprints and interior points are statistical locations, not newly detected panel polygons.")
    add("These rules prefer roofs with usable geometric capacity over unrestricted random placement. Actual improvement in location accuracy over random allocation has not been demonstrated because exact small-system locations are unavailable. Updated municipal aerial surveys or authorized location evidence are the preferred additional inputs. Scoring uses frozen DOP20 imagery; municipal-flight and satellite imagery are outside the input scope.","SmallPV")
    add("Storage is attached through MaStR location relationships","SectionPV")
    table(["Measure", "Snapshot value"],[
        ["Supported PV-storage relationship rows",len(context["storage_relationships"])],
        ["Unique storage units in supported relationships",s["unique_supported_storage_units"]],
        ["Unique storage systems in supported relationships",s["unique_supported_storage_systems"]],
        ["Unique storage-unit net power",f"{s['unique_storage_power_kw']:,.3f} kW"],
        ["Unique storage-system usable capacity",f"{s['unique_storage_capacity_kwh']:,.3f} kWh"]],[WIDTH*.7,WIDTH*.3])
    add("Power uses EinheitenStromSpeicher.Nettonennleistung; usable energy uses AnlagenStromSpeicher.NutzbareSpeicherkapazitaet. Matching requires nonempty shared location IDs and reciprocal source relationships. Storage/system identifiers are retained, with a shared-storage flag and no exclusive PV allocation.")
    add("Flat PV rows contain convenient location-linked storage power and capacity, deduplicated within each PV row. Never sum these columns across PV rows without deduplicating storage-unit and storage-system IDs. The totals above cover all supported snapshot relationships, including context PV, not only the anonymous target cohort.","SmallPV")
    page()

    heading(7,"GeoPackage, web atlas and editable annotations")
    table(["Layer / table", "Meaning"],[
        ["associated_pv_points", "All selected result points, with explicit association status, score, confidence, reason, flattened technical attributes and storage values."],
        ["inferred_pv_arrays", "Image-array geometry selected by the constrained scorer. Multiple same-location registrations can reference the same geometry."],
        ["statistical_building_allocations", "Building footprint allocations for later records. These geometries are not observed panel outlines."],
        ["scored_array_groups", "Every original proposed group, its strongest raw registry score when eligible, and selected-registration count."],
        ["unselected_image_groups", "Image groups without a selected MaStR registration; this is not evidence of unregistered installations."],
        ["registry_outcomes / mastr_solar_attributes", "All 3,476 records, including unlocated outcomes; technical source fields and scored alternatives."],
        ["storage_relationships / units / systems", "Normalized, nonexclusive storage relationships and technical source attributes."],
        ["provenance / municipalities", "Frozen input hashes, scoring policy, snapshot identities and official study boundaries."]],[WIDTH*.39,WIDTH*.61])
    add("Result web application","SectionPV")
    add("The atlas opens on anonymous ≤30-kW targets. Search and filter by municipality, evidence type, confidence and minimum power. Click a point or directory entry to see selected geometry, MaStR technical fields, storage values and reasons. Official RGB/CIR WMS and OpenStreetMap are selectable. Detailed buildings and raw detected arrays appear at zoom 16 and above; contextual rendering is bounded.")
    add("Confidence and reason editing","SectionPV")
    add("The local result server persists versioned confidence/reason annotations in a separate SQLite database. Stale writes are rejected. These edits cannot set verified identity. The edited GeoPackage endpoint includes annotation events and current values; the original PDF and original downloadable GeoPackage remain the generated snapshot. Static HTTP serving offers read-only exploration.")
    add("Confidence interpretation: medium = a nearby public coordinate with a sufficient selected-score gap and no temporal conflict; low = inferred, non-independent location evidence; very low = statistical allocation; unlocated = no selected position. Manual confidence edits are annotations, not newly calibrated probabilities.","SmallPV")
    page()

    heading(8,"Verification, limitations and next evidence")
    table(["Check", "Observed result"],[
        ["Registry completeness", "3,476 unique IDs retained; 2,318 strict-under-30 targets retained."],
        ["Spatial export", "All six spatial layers round-trip in EPSG:3035 with valid geometry; SQLite integrity check passes."],
        ["Allocation invariants", "Result points lie within their geometry; selected score agrees with the first feasible alternative; no power-screen violations or cross-location group sharing."],
        ["Technical privacy filter", "Only allowlisted technical source fields are delivered. No operator identifiers, street addresses or source XML."],
        ["WMS requests", "Both official RGB and CIR GetCapabilities contain the configured layer; both tested GetMap responses are nonblank 256 × 256 images."],
        ["Annotation workflow", "Isolated test database verifies persistence, stale-write rejection, unknown-ID rejection, restricted states and annotated GeoPackage export."],
        ["Software validation", "56 automated tests cover registry processing, matching, review workflows, scoring, pairwise location consistency, chain drift, shared-building reuse and remaining roof capacity."]],[WIDTH*.3,WIDTH*.7])
    add("What has not been established","SectionPV")
    add("There is no independent representative small-roof identity ground truth, and the production image review remains unreviewed. Municipal detection precision/recall, array completeness, accepted-association accuracy and confidence calibration are unmeasured. Numeric score gaps can overstate evidence when priors or geometry are uncertain. A greedy allocation introduces ordering effects and cannot distinguish all similar systems.")
    add("Building footprints are horizontal geometry, not measured roof planes. The fallback assumes a usable roof fraction and has no observed panels. Multi-roof systems, extensions, shadow, façade PV, missed arrays and incorrect registry module counts remain relevant. Shared location and storage relations do not independently validate the inferred building.")
    add("Highest-value next evidence","SectionPV")
    add("Obtain authorized exact location samples in both municipalities; independently check positives and model-negative roofs; validate grouping and technical priors on held-out buildings; acquire actual roof-plane slope/facing information if available; use newer, georeferenced municipal flights for later systems. Replace statistical locations when direct evidence arrives, retaining provenance.")
    add("Verification details, WMS evidence and artifact checksums are recorded in the project metadata. Browser checks cover result lists, image and statistical details, municipality/evidence filters, storage display and live RGB/CIR imagery in the narrow responsive layout. Editing/export persistence is tested through an isolated API database. Manual-geometry rebuild compatibility and concurrent edit/export snapshot consistency remain operational limitations. These checks assess application behavior, not geolocation accuracy.","SmallPV")
    page()

    heading(9,"Sources, reproduction and audit identifiers")
    sources=[
        ("Official MaStR daily export","https://download.marktstammdatenregister.de/Gesamtdatenexport_20261115_26.2.zip"),
        ("LGB DOP20 RGB WMS","https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms"),
        ("LGB DOP20 CIR WMS","https://isk.geobasis-bb.de/mapproxy/dop20cir/service/wms"),
        ("Official DOP download metadata","https://geobroker.geobasis-bb.de/gbss.php?MODE=GetProductInformation&amp;PRODUCTID=253b7d3d-6b42-47dc-b127-682de078b7ae"),
        ("Brandenburg model repository","https://huggingface.co/Muemmel/solar-pv-segmentation-brandenburg"),
        ("Optional open-MaStR tooling","https://github.com/OpenEnergyPlatform/open-MaStR/")]
    for label,url in sources:
        add(f"<b>{label}</b><br/><link href='{url}' color='#315b49'>{url}</link>","SmallPV")
    add("Model-comparison evidence","SectionPV")
    add("Local pilot: lab/review-app/dist/data/project.json (sibling lab checkout). Public benchmark: packages/model-diff/results/evaluation.json. Both are summarized, with hashes, in model-comparison.json; the local pilot summary remains bundled even when the sibling lab is absent.","SmallPV")
    add("Reproduction from repository root","SectionPV")
    for command in ["uv run --package roof-pv-review python packages/result-web-app/build.py",
                    "uv run --package roof-pv-review python packages/result-web-app/verify.py",
                    "uv run --package roof-pv-review --with reportlab python packages/result-web-app/report.py",
                    "uv run --package roof-pv-review python packages/result-web-app/serve.py --port 8790"]:
        add(escape(command),"SmallPV")
    add("Input and output identifiers","SectionPV")
    for label,value in [("Image run",metadata["run_id"]),("Registry snapshot",metadata["registry_snapshot"]),("Source batch",metadata["source_batch"]),
                        ("Run manifest SHA-256",metadata["source_hashes"]["run_manifest"]),("Registry manifest SHA-256",metadata["source_hashes"]["registry_manifest"]),
                        ("GeoPackage SHA-256",digest(directory/"roof-pv-results.gpkg"))]:
        add(f"<b>{label}</b><br/>{escape(value)}","SmallPV")
    add("Attribution: imagery and cadastral context © GeoBasis-DE/LGB, dl-de/by-2-0; MaStR © Bundesnetzagentur, dl-de/by-2-0; Brandenburg model weights CC-BY-4.0; optional basemap © OpenStreetMap contributors. Live services were checked for this delivery; availability and current image epochs may change.","SmallPV")

    def decorate(canvas,doc):
        canvas.saveState();canvas.setStrokeColor(colors.HexColor("#d8dfd3"));canvas.setLineWidth(.5)
        canvas.line(48,A4[1]-35,A4[0]-48,A4[1]-35)
        canvas.setFont("Helvetica-Bold",7);canvas.setFillColor(GREEN);canvas.drawString(48,A4[1]-25,"ROOF PV ATLAS / PROJECT REPORT")
        canvas.setFont("Helvetica",7);canvas.setFillColor(MUTED);canvas.drawString(48,27,"17 NOV 2026 / INFERRED LOCATIONS - IDENTITY ACCURACY UNMEASURED")
        canvas.drawRightString(A4[0]-48,27,str(doc.page));canvas.restoreState()
    path=directory/"roof-pv-report.pdf"
    doc=SimpleDocTemplate(str(path),pagesize=A4,rightMargin=48,leftMargin=48,topMargin=55,bottomMargin=48,
                          title="Rooftop PV geolocation - Hohen Neuendorf and Birkenwerder",author="",subject="Technical associations, storage and uncertainty")
    doc.build(story,onFirstPage=decorate,onLaterPages=decorate)
    print(f"Created {path.name}")


if __name__=="__main__":
    generate(APP/"dist")
