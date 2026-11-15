import type { RegistryUnitDetail } from "./types";

interface Props {
  detail: RegistryUnitDetail;
  t: (key: string) => string;
}

export function RegistrationDetails({ detail, t }: Props) {
  const { unit, raw } = detail;
  const missing = t("registrationUnavailable");
  function value(input: unknown, suffix = "") {
    return input === null || input === undefined || input === ""
      ? missing
      : `${String(input)}${suffix}`;
  }
  function decoded(field: string) {
    const code = raw[field];
    if (!code) return missing;
    const key = `mastrValue_${code}`;
    const translated = t(key);
    return translated === key ? unit.decoded[field] || value(code) : translated;
  }
  function boolean(field: string) {
    const input = raw[field];
    return input === "1" || input === "true"
      ? t("registrationYes")
      : input === "0" || input === "false"
        ? t("registrationNo")
        : missing;
  }
  function fields(rows: [string, string][]) {
    return (
      <dl className="registration-detail-fields">
        {rows.map(([label, content]) => (
          <div key={label}>
            <dt>{t(label)}</dt>
            <dd>{content}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return (
    <section
      className="registration-details"
      aria-label={t("registrationDetailsTitle")}
    >
      <h3>{unit.id}</h3>
      <p className="registration-name">
        {value(raw.NameStromerzeugungseinheit)}
      </p>
      <div className="registration-summary">
        <strong>{value(unit.power_kw, " kWp")}</strong>
        <span>
          {value(unit.module_count)} {t("moduleCountShort")}
        </span>
        <span>{decoded("EinheitBetriebsstatus")}</span>
      </div>
      <h4>{t("registrationTechnical")}</h4>
      {fields([
        ["registrationSolarType", decoded("ArtDerSolaranlage")],
        ["registrationGrossPower", value(unit.power_kw, " kWp")],
        ["registrationNetPower", value(raw.Nettonennleistung, " kW")],
        [
          "registrationInverterPower",
          value(raw.ZugeordneteWirkleistungWechselrichter, " kW"),
        ],
        ["moduleCount", value(unit.module_count)],
      ])}
      <h4>{t("registrationDates")}</h4>
      {fields([
        ["registrationOperatingStatus", decoded("EinheitBetriebsstatus")],
        ["commissioned", value(unit.commissioned)],
        ["registrationRegisteredAt", value(raw.Registrierungsdatum)],
        [
          "registrationUpdatedAt",
          value(raw.DatumLetzteAktualisierung?.replace("T", " ")),
        ],
      ])}
      <h4>{t("registrationDirections")}</h4>
      {fields([
        ["mainDirection", decoded("Hauptausrichtung")],
        ["mainInclination", decoded("HauptausrichtungNeigungswinkel")],
        ["secondaryDirection", decoded("Nebenausrichtung")],
        [
          "registrationSecondaryInclination",
          decoded("NebenausrichtungNeigungswinkel"),
        ],
        [
          "registrationUniformOrientation",
          boolean("EinheitlicheAusrichtungUndNeigungswinkel"),
        ],
      ])}
      <h4>{t("registrationLocation")}</h4>
      {fields([
        ["registrationMunicipality", value(unit.municipality)],
        ["registrationMunicipalityCode", value(raw.Gemeindeschluessel)],
        ["registrationPostcode", value(unit.postcode)],
        ["registrationTown", value(raw.Ort)],
        ["registrationDistrict", value(raw.Landkreis)],
        ["registrationStreet", value(raw.Strasse)],
        ["registrationHouseNumber", value(raw.Hausnummer)],
        ["registrationLongitude", value(unit.longitude ?? raw.Laengengrad)],
        ["registrationLatitude", value(unit.latitude ?? raw.Breitengrad)],
      ])}
      <h4>{t("registrationIdentifiers")}</h4>
      {fields([
        ["registrationSolarId", unit.id],
        ["registrationLocationId", value(raw.LokationMaStRNummer)],
        ["registrationEegId", value(raw.EegMaStRNummer)],
        ["registrationOperatorId", value(raw.AnlagenbetreiberMastrNummer)],
        ["registrationSnapshot", detail.snapshot_id],
      ])}
      <h4>{t("storageRelationships")}</h4>
      {fields([
        ["registrationDeclaredStorage", boolean("SpeicherAmGleichenOrt")],
      ])}
      {detail.storage.length ? (
        <>
          <p className="fine-print">{t("storageNoAllocation")}</p>
          {detail.storage.map((storage) => (
            <div className="registration-storage" key={storage.storage_id}>
              <b>{storage.storage_id}</b>
              <p>{storage.system_id}</p>
              <p>
                {t("registrationStoragePower")}:{" "}
                {value(storage.net_power_kw, " kW")}
              </p>
              <p>
                {t("registrationStorageCapacity")}:{" "}
                {value(storage.usable_capacity_kwh, " kWh")}
              </p>
              <p>{t("storage_" + storage.relationship_status)}</p>
            </div>
          ))}
        </>
      ) : (
        <p className="fine-print">{t("registrationNoStorageRecords")}</p>
      )}
      {unit.flags.length > 0 && (
        <div className="registration-warnings">
          {unit.flags.map((flag) => (
            <p key={flag}>{t("flag_" + flag)}</p>
          ))}
        </div>
      )}
      <details className="registration-raw">
        <summary>
          {t("registrationOriginalFields")} ({Object.keys(raw).length})
        </summary>
        <dl className="registration-detail-fields raw-fields">
          {Object.entries(raw).map(([key, input]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{value(input)}</dd>
            </div>
          ))}
        </dl>
      </details>
    </section>
  );
}
