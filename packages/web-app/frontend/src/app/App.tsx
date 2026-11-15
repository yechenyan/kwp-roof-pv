import { useCallback, useEffect, useMemo, useState } from "react";
import type { FeatureCollection } from "geojson";
import { request, runUrl, type ReviewUpdate } from "../api";
import { CandidateQueue } from "../features/candidates/CandidateQueue";
import { ExportPanel } from "../features/exports/ExportPanel";
import { MapPanel } from "../features/map/MapPanel";
import { readReviewer, writeReviewer } from "../preferences";
import type {
  Candidate,
  Mode,
  ReviewGeometry,
  Run,
  Snapshot,
  Status,
  DrawnGeometry,
  CandidateProperties,
} from "../types";
import en from "../locales/en.json";
import zh from "../locales/zh.json";

export function App() {
  const [language, setLanguage] = useState<"en" | "zh">("zh");
  const t = useCallback(
    (key: string) =>
      ((language === "zh" ? zh : en) as Record<string, string>)[key] || key,
    [language],
  );
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [observations, setObservations] = useState<{
    rgbi?: FeatureCollection;
    rgb?: FeatureCollection;
    buildings?: FeatureCollection;
  }>({});
  const [statusFilter, setStatusFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [buildingFilter, setBuildingFilter] = useState("all");
  const [reviewer, setReviewer] = useState(readReviewer);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [mounting, setMounting] =
    useState<NonNullable<CandidateProperties["mounting"]>>("unknown");
  const [draft, setDraft] = useState<ReviewGeometry | null>(null);
  const [mode, setMode] = useState<Mode>("none");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [download, setDownload] = useState("");
  const [reportUrl, setReportUrl] = useState("");
  const [checkedIds, setCheckedIds] = useState<string[]>([]);
  const [jobs, setJobs] = useState<
    { name: string; status: string; completed: number; total: number }[]
  >([]);
  const selected = snapshot?.candidates.features.find(
    (f) => f.id === selectedId,
  );
  const dirty = Boolean(
    draft ||
    reason !== (selected?.properties.reason || "") ||
    note !== (selected?.properties.note || "") ||
    mounting !== (selected?.properties.mounting || "unknown"),
  );

  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  }, [language]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  useEffect(() => {
    request<Run[]>("/api/runs")
      .then((data) => {
        setRuns(data);
        setRunId(data.at(-1)?.run_id || "");
      })
      .catch((e) => setError(String(e)));
  }, []);

  const refresh = useCallback(async (id: string) => {
    const data = await request<Snapshot>(runUrl(id));
    setSnapshot(data);
    setSelectedId((previous) =>
      data.candidates.features.some((f) => f.id === previous)
        ? previous
        : data.candidates.features[0]?.properties.id || "",
    );
    return data;
  }, []);

  useEffect(() => {
    const poll = () =>
      request<unknown>("/api/jobs")
        .then((value) => setJobs(Array.isArray(value) ? value : []))
        .catch(() => {});
    void poll();
    const interval = window.setInterval(poll, 5000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!runId) return;
    let active = true;
    setSnapshot(null);
    setObservations({});
    setCheckedIds([]);
    clearExports();
    setDraft(null);
    setMode("none");
    Promise.all([
      request<Snapshot>(runUrl(runId)),
      request<FeatureCollection>(`${runUrl(runId)}/assets/rgbi.geojson`),
      request<FeatureCollection>(`${runUrl(runId)}/assets/rgb.geojson`),
      request<FeatureCollection>(
        `${runUrl(runId)}/assets/buildings.geojson`,
      ).catch(() => ({ type: "FeatureCollection" as const, features: [] })),
    ])
      .then(([data, rgbi, rgb, buildings]) => {
        if (!active) return;
        setSnapshot(data);
        setObservations({ rgbi, rgb, buildings });
        setSelectedId(
          data.manifest.kind === "municipal"
            ? ""
            : data.candidates.features[0]?.properties.id || "",
        );
      })
      .catch((e) => {
        if (active) setError(String(e));
      });
    return () => {
      active = false;
    };
  }, [runId]);

  useEffect(() => {
    setReason(selected?.properties.reason || "");
    setNote(selected?.properties.note || "");
    setMounting(selected?.properties.mounting || "unknown");
  }, [selected?.id, selected?.properties.version]);

  function select(id: string) {
    if (busy || (dirty && !window.confirm(t("discard")))) return;
    setSelectedId(id);
    setDraft(null);
    setMode("none");
    setMessage("");
  }

  function clearExports() {
    setDownload("");
    setReportUrl("");
  }

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  function save(status: Status, resetGeometry = false) {
    if (!selected) return;
    if (!reviewer.trim()) {
      setError(t("needReviewer"));
      return;
    }
    if (["rejected", "uncertain"].includes(status) && !reason.trim()) {
      setError(t("missingReason"));
      return;
    }
    void perform(async () => {
      const update: ReviewUpdate = {
        expected_version: selected.properties.version,
        status,
        reviewer: reviewer.trim(),
        reason,
        note,
        mounting,
        geometry: resetGeometry ? undefined : draft,
        reset_geometry: resetGeometry,
      };
      await request(
        `${runUrl(runId)}/candidates/${encodeURIComponent(selected.properties.id)}`,
        {
          method: "PATCH",
          body: JSON.stringify(update),
        },
      );
      await refresh(runId);
      setDraft(null);
      setMode("none");
      setMessage(t("saved"));
      clearExports();
    });
  }

  function finish(geometry: DrawnGeometry) {
    if (!reviewer.trim()) {
      setError(t("needReviewer"));
      return;
    }
    void perform(async () => {
      if (mode === "split") {
        if (!selected) return;
        const result = await request<{ ids: string[] }>(
          `${runUrl(runId)}/transform`,
          {
            method: "POST",
            body: JSON.stringify({
              operation: "split",
              expected_versions: {
                [selected.properties.id]: selected.properties.version,
              },
              cut_line: geometry,
              reviewer: reviewer.trim(),
              note,
            }),
          },
        );
        await refresh(runId);
        setSelectedId(result.ids[0]);
        setCheckedIds([]);
        setMode("none");
        setDraft(null);
        clearExports();
        setMessage(t("transformed"));
        return;
      }
      const endpoint = mode === "area" ? "reviewed-areas" : "candidates";
      const result = await request<{ id: string }>(
        `${runUrl(runId)}/${endpoint}`,
        {
          method: "POST",
          body: JSON.stringify({ geometry, reviewer: reviewer.trim(), note }),
        },
      );
      await refresh(runId);
      if (mode !== "area") setSelectedId(result.id);
      setMessage(t(mode === "area" ? "checked" : "added"));
      setMode("none");
      setDraft(null);
      clearExports();
    });
  }

  function mergeChecked() {
    if (!reviewer.trim()) {
      setError(t("needReviewer"));
      return;
    }
    void perform(async () => {
      const expected = Object.fromEntries(
        snapshot!.candidates.features
          .filter((f) => checkedIds.includes(f.properties.id))
          .map((f) => [f.properties.id, f.properties.version]),
      );
      const result = await request<{ ids: string[] }>(
        `${runUrl(runId)}/transform`,
        {
          method: "POST",
          body: JSON.stringify({
            operation: "merge",
            expected_versions: expected,
            reviewer: reviewer.trim(),
            note,
          }),
        },
      );
      await refresh(runId);
      setSelectedId(result.ids[0]);
      setCheckedIds([]);
      clearExports();
      setMessage(t("transformed"));
    });
  }

  function undoLast() {
    if (!reviewer.trim()) {
      setError(t("needReviewer"));
      return;
    }
    void perform(async () => {
      await request(`${runUrl(runId)}/undo`, {
        method: "POST",
        body: JSON.stringify({
          expected_revision: snapshot!.revision,
          reviewer: reviewer.trim(),
        }),
      });
      await refresh(runId);
      setCheckedIds([]);
      setDraft(null);
      setMode("none");
      clearExports();
      setMessage(t("undone"));
    });
  }

  const filtered: Candidate[] = useMemo(
    () =>
      snapshot?.candidates.features.filter(
        (f) =>
          (statusFilter === "all" || f.properties.status === statusFilter) &&
          (categoryFilter === "all" ||
            f.properties.category === categoryFilter) &&
          (buildingFilter === "all" ||
            (buildingFilter === "with"
              ? (f.properties.building_overlap_ratio || 0) > 0
              : buildingFilter === "without"
                ? f.properties.building_overlap_ratio === 0
                : f.properties.building_overlap_ratio === undefined)),
      ) || [],
    [snapshot, statusFilter, categoryFilter, buildingFilter],
  );
  const counts =
    snapshot?.candidates.features.reduce<Record<string, number>>(
      (result, feature) => {
        result[feature.properties.status] =
          (result[feature.properties.status] || 0) + 1;
        return result;
      },
      {},
    ) || {};
  const progress = snapshot?.candidates.features.length
    ? Math.round(
        (100 *
          ((counts.confirmed || 0) +
            (counts.rejected || 0) +
            (counts.uncertain || 0))) /
          snapshot.candidates.features.length,
      )
    : 0;

  return (
    <div className="workspace">
      <header className="masthead">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <i />
            <i />
            <i />
            <i />
          </span>
          <div>
            <h1>
              {t("title")}
              <span> / FIELDWORK</span>
            </h1>
            <p>{t("subtitle")}</p>
          </div>
        </div>
        <div className="header-actions">
          <span className="eyebrow">{t("scope")}</span>
          <button
            className="language"
            onClick={() => setLanguage(language === "zh" ? "en" : "zh")}
          >
            {t("languageSwitch")}
          </button>
        </div>
      </header>
      <main>
        {jobs.length > 0 && (
          <section className="jobs-panel">
            {jobs.map((job) => (
              <div key={job.name}>
                <b>{job.name}</b>
                <span>
                  {job.completed} / {job.total} · {job.status}
                </span>
                <progress max={job.total || 1} value={job.completed} />
              </div>
            ))}
            <button
              onClick={() =>
                void request<Run[]>("/api/runs")
                  .then(setRuns)
                  .catch((e) => setError(String(e)))
              }
            >
              {t("refreshRuns")}
            </button>
          </section>
        )}
        <div className="context-bar">
          <div>
            <span className="eyebrow">BRANDENBURG · DOP20</span>
            <h2>
              {snapshot?.manifest.input.name
                ? `${snapshot.manifest.input.name} · ${(snapshot.manifest.input.area_m2! / 1e6).toFixed(2)} km²`
                : t("sample")}
            </h2>
          </div>
          <label>
            {t("run")}
            <select
              disabled={busy}
              value={runId}
              onChange={(e) => {
                if (!dirty || window.confirm(t("discard")))
                  setRunId(e.target.value);
              }}
            >
              {runs.map((run) => (
                <option key={run.run_id} value={run.run_id}>
                  {run.run_id}
                </option>
              ))}
            </select>
          </label>
          <div className="revision">
            <span>{t("revision")}</span>
            <strong>r{snapshot?.revision ?? "—"}</strong>
          </div>
        </div>
        {error && (
          <div role="alert" className="notice error">
            <strong>{t("error")}</strong>
            <span>{error}</span>
            <small>{t("refreshHint")}</small>
            <button
              disabled={!runId || busy}
              onClick={() =>
                void perform(async () => {
                  await refresh(runId);
                })
              }
            >
              {t("reload")}
            </button>
          </div>
        )}
        {message && (
          <div role="status" className="notice success">
            {message}
          </div>
        )}
        {!snapshot ? (
          <div className="empty-panel">
            {runs.length ? t("loading") : t("noRuns")}
          </div>
        ) : (
          <>
            <section className="stats">
              <div>
                <span>{t("candidates")}</span>
                <strong>
                  {snapshot.candidates.features.length
                    .toString()
                    .padStart(2, "0")}
                </strong>
                <small>RGBI + RGB</small>
              </div>
              {(["confirmed", "pending", "uncertain", "rejected"] as const).map(
                (status) => (
                  <div key={status}>
                    <span>
                      <i className={`dot ${status}`} />
                      {t(status)}
                    </span>
                    <strong>{counts[status] || 0}</strong>
                  </div>
                ),
              )}
              <div className="progress-stat">
                <span>{t("acquired")}</span>
                <strong className="date">
                  {snapshot.manifest.input.acquisition_date}
                </strong>
                <div className="progress">
                  <i style={{ width: `${progress}%` }} />
                </div>
                <small>{progress}% reviewed</small>
              </div>
            </section>
            <div className="work-grid">
              <div className="map-column">
                <MapPanel
                  snapshot={snapshot}
                  candidates={filtered}
                  selected={selected}
                  observations={observations}
                  mode={mode}
                  draft={draft}
                  onSelect={select}
                  onDraft={setDraft}
                  onFinish={finish}
                  onCancel={() => {
                    setMode("none");
                    setDraft(null);
                  }}
                  t={t}
                />
                <div className="map-actions">
                  <button
                    disabled={
                      busy || dirty || checkedIds.length < 2 || mode !== "none"
                    }
                    onClick={mergeChecked}
                  >
                    {t("merge")} ({checkedIds.length})
                  </button>
                  <button
                    disabled={busy || dirty || !selected || mode !== "none"}
                    onClick={() => setMode("split")}
                  >
                    {t("split")}
                  </button>
                  <button
                    disabled={
                      busy ||
                      dirty ||
                      snapshot.revision === 0 ||
                      snapshot.events.at(-1)?.kind === "action_undone"
                    }
                    onClick={undoLast}
                  >
                    {t("undo")}
                  </button>
                  {checkedIds.length > 0 && (
                    <button onClick={() => setCheckedIds([])}>
                      {t("clearSelection")}
                    </button>
                  )}
                  <button
                    disabled={busy || mode !== "none"}
                    onClick={() => setMode("draw")}
                  >
                    ＋ {t("draw")}
                  </button>
                  <button
                    disabled={busy || mode !== "none"}
                    onClick={() => setMode("area")}
                  >
                    ▧ {t("checkArea")}
                  </button>
                  <label className="reviewer-field">
                    {t("reviewer")}
                    <input
                      value={reviewer}
                      placeholder={t("reviewerHint")}
                      onChange={(e) => {
                        setReviewer(e.target.value);
                        writeReviewer(e.target.value);
                      }}
                    />
                  </label>
                </div>
                <CandidateQueue
                  key={runId}
                  candidates={filtered}
                  total={snapshot.candidates.features.length}
                  selectedId={selectedId}
                  checkedIds={checkedIds}
                  filters={{
                    status: statusFilter,
                    category: categoryFilter,
                    building: buildingFilter,
                  }}
                  onFilter={(key, value) => {
                    ({
                      status: setStatusFilter,
                      category: setCategoryFilter,
                      building: setBuildingFilter,
                    })[key](value);
                  }}
                  onSelect={select}
                  onCheck={(id, checked) =>
                    setCheckedIds((previous) =>
                      checked
                        ? [...previous, id]
                        : previous.filter((value) => value !== id),
                    )
                  }
                  t={t}
                />
              </div>
              <aside className="inspector">
                <div className="section-title">
                  <h2>{t("selected")}</h2>
                  {selected && (
                    <span
                      className={`status-tag ${selected.properties.status}`}
                    >
                      {t(selected.properties.status)}
                    </span>
                  )}
                </div>
                {!selected ? (
                  <p>{t("select")}</p>
                ) : (
                  <>
                    <div className="candidate-number">
                      #{selected.properties.id.split(":").at(-1)?.slice(-8)}
                    </div>
                    <p className="category-label">
                      {t(selected.properties.category)}
                    </p>
                    <dl>
                      <div>
                        <dt>{t("area")}</dt>
                        <dd>
                          {selected.properties.projected_area_m2.toFixed(2)}{" "}
                          <small>m²</small>
                        </dd>
                      </div>
                      <div>
                        <dt>{t("model")}</dt>
                        <dd>
                          {selected.properties.default_model?.toUpperCase() ||
                            t("manual")}
                        </dd>
                      </div>
                      <div>
                        <dt>{t("score")}</dt>
                        <dd>
                          {selected.properties.score_mean?.toFixed(3) ?? "—"}
                        </dd>
                      </div>
                    </dl>
                    <p className="fine-print">{t("uncalibrated")}</p>
                    {selected.properties.boundary_clipped && (
                      <p className="evidence-line">{t("boundaryClipped")}</p>
                    )}
                    <label className="input-label">
                      {t("mounting")}
                      <select
                        aria-label={t("mounting")}
                        value={mounting}
                        disabled={busy}
                        onChange={(event) =>
                          setMounting(
                            event.target.value as NonNullable<
                              CandidateProperties["mounting"]
                            >,
                          )
                        }
                      >
                        {["unknown", "roof", "ground", "other"].map((value) => (
                          <option key={value} value={value}>
                            {t("mounting_" + value)}
                          </option>
                        ))}
                      </select>
                    </label>
                    {selected.properties.acquisition_dates && (
                      <p className="evidence-line">
                        {t("acquired")}:{" "}
                        {selected.properties.acquisition_dates.join(", ")}
                      </p>
                    )}
                    {selected.properties.building_overlap_ratio !==
                      undefined && (
                      <p className="evidence-line">
                        {t("buildingOverlap")}:{" "}
                        {(
                          selected.properties.building_overlap_ratio * 100
                        ).toFixed(1)}
                        % · {selected.properties.building_ids?.length || 0}{" "}
                        {t("buildings")}
                      </p>
                    )}
                    {selected.properties.geometry_modified && (
                      <span className="pill">{t("edited")}</span>
                    )}
                    <div className="geometry-actions">
                      <button
                        disabled={busy || mode !== "none"}
                        onClick={() => setMode("edit")}
                      >
                        {t("edit")}
                      </button>
                      <button
                        disabled={
                          busy || !selected.properties.geometry_modified
                        }
                        onClick={() => save(selected.properties.status, true)}
                      >
                        {t("restore")}
                      </button>
                    </div>
                    {draft && <p className="draft-label">● {t("draft")}</p>}
                    <label className="input-label">
                      {t("reason")}
                      <input
                        aria-label={t("reason")}
                        disabled={busy}
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        placeholder={t("reasonHint")}
                      />
                    </label>
                    <label className="input-label">
                      {t("note")}
                      <textarea
                        aria-label={t("note")}
                        rows={4}
                        disabled={busy}
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                      />
                    </label>
                    <div className="review-buttons">
                      <button
                        className="primary"
                        disabled={busy}
                        onClick={() => save("confirmed")}
                      >
                        ✓ {t("confirm")}
                      </button>
                      <div>
                        <button
                          disabled={busy}
                          onClick={() => save("rejected")}
                        >
                          {t("reject")}
                        </button>
                        <button
                          disabled={busy}
                          onClick={() => save("uncertain")}
                        >
                          {t("unsure")}
                        </button>
                      </div>
                      <button
                        disabled={busy}
                        onClick={() => save(selected.properties.status)}
                      >
                        {t("saveShape")}
                      </button>
                      <button
                        className="text-button"
                        disabled={busy}
                        onClick={() => save("pending")}
                      >
                        {t("reset")}
                      </button>
                    </div>
                  </>
                )}
                <ExportPanel
                  busy={busy}
                  dirty={dirty}
                  disabled={mode !== "none"}
                  download={download}
                  reportUrl={reportUrl}
                  t={t}
                  onExport={() =>
                    void perform(async () => {
                      const result = await request<{
                        download_url: string;
                        report_url?: string;
                      }>(`${runUrl(runId)}/exports`, { method: "POST" });
                      setDownload(result.download_url);
                      setReportUrl(result.report_url || "");
                    })
                  }
                />
              </aside>
            </div>
            <div className="bottom-grid">
              <section className="provenance">
                <h3>{t("limits")}</h3>
                <p>{t("limitsText")}</p>
                <div className="hashes">
                  <span>
                    RGBI ·{" "}
                    {snapshot.manifest.identity.config.models.rgbi.variant}
                  </span>
                  <code>
                    {snapshot.manifest.identity.config.models.rgbi.sha256.slice(
                      0,
                      16,
                    )}
                    …
                  </code>
                  <span>
                    RGB · {snapshot.manifest.identity.config.models.rgb.variant}
                  </span>
                  <code>
                    {snapshot.manifest.identity.config.models.rgb.sha256.slice(
                      0,
                      16,
                    )}
                    …
                  </code>
                  <span>{t("coverage")}</span>
                  <code>
                    {snapshot.manifest.input.valid_pixels} /{" "}
                    {snapshot.manifest.input.missing_pixels}
                  </code>
                </div>
              </section>
              <section className="audit">
                <h3>{t("history")}</h3>
                {snapshot.events
                  .slice(-5)
                  .reverse()
                  .map((event) => (
                    <div key={event.revision}>
                      <b>r{event.revision}</b>
                      <span>
                        {event.kind}
                        <small>
                          {event.reviewer} ·{" "}
                          {new Date(event.created_at).toLocaleString()}
                        </small>
                      </span>
                    </div>
                  ))}
              </section>
            </div>
          </>
        )}
      </main>
      <footer>ROOF PV / Brandenburg · RGBI + RGB · {t("attribution")}</footer>
    </div>
  );
}
