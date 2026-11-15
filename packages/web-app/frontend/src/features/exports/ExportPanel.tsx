interface Props {
  busy: boolean;
  dirty: boolean;
  disabled: boolean;
  download: string;
  reportUrl: string;
  onExport: () => void;
  t: (key: string) => string;
}

export function ExportPanel(props: Props) {
  return (
    <div className="export-panel">
      <h3>GeoPackage</h3>
      <p>{props.t("exportHint")}</p>
      {props.dirty && <p>{props.t("saveBeforeExport")}</p>}
      <button
        className="dark-button"
        disabled={props.busy || props.dirty || props.disabled}
        onClick={props.onExport}
      >
        {"↓ " + props.t("export")}
      </button>
      {props.reportUrl && (
        <a
          className="download-link"
          href={props.reportUrl}
          target="_blank"
          rel="noreferrer"
        >
          {props.t("viewReport")}
        </a>
      )}
      {props.download && (
        <a className="download-link" href={props.download}>
          {props.t("download")}
        </a>
      )}
    </div>
  );
}
