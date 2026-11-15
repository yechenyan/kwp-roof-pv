import { useEffect, useState } from "react";
import type { IdentityReview } from "../../api";
import type { IdentityDecision } from "./types";

interface Props {
  previous?: IdentityDecision;
  disabled: boolean;
  onDirty: (dirty: boolean) => void;
  onSave: (
    values: Pick<
      IdentityReview,
      "state" | "evidence_type" | "evidence" | "note" | "group_confirmed"
    >,
  ) => void;
  t: (key: string) => string;
}
export function IdentityForm({
  previous,
  disabled,
  onDirty,
  onSave,
  t,
}: Props) {
  const [state, setState] = useState<IdentityReview["state"]>(
    previous?.state || "ambiguous",
  );
  const [source, setSource] = useState<
    NonNullable<IdentityReview["evidence_type"]>
  >(previous?.evidence_type || "attribute_comparison");
  const [evidence, setEvidence] = useState(previous?.evidence || "");
  const [note, setNote] = useState(previous?.note || "");
  const [confirmed, setConfirmed] = useState(
    Boolean(previous?.group_confirmed),
  );
  const dirty =
    state !== (previous?.state || "ambiguous") ||
    source !== (previous?.evidence_type || "attribute_comparison") ||
    evidence !== (previous?.evidence || "") ||
    note !== (previous?.note || "") ||
    confirmed !== Boolean(previous?.group_confirmed);
  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  return (
    <fieldset className="identity-form" disabled={disabled}>
      <legend>{t("identityDecision")}</legend>
      <label>
        {t("identityDecision")}
        <select
          aria-label={t("identityDecision")}
          value={state}
          onChange={(e) => setState(e.target.value as IdentityReview["state"])}
        >
          {[
            "ambiguous",
            "probable_assignment",
            "verified_identity",
            "rejected",
            "pending",
          ].map((v) => (
            <option key={v} value={v}>
              {t("identity_" + v)}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t("identityEvidenceType")}
        <select
          aria-label={t("identityEvidenceType")}
          value={source}
          onChange={(e) =>
            setSource(
              e.target.value as NonNullable<IdentityReview["evidence_type"]>,
            )
          }
        >
          {[
            "attribute_comparison",
            "authorized_address",
            "operator_confirmation",
            "field_verification",
            "registry_coordinate_review",
            "none",
          ].map((v) => (
            <option key={v} value={v}>
              {t("evidence_" + v)}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t("identityEvidence")}
        <textarea
          aria-label={t("identityEvidence")}
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
          rows={3}
        />
      </label>
      <details>
        <summary>{t("identityMoreOptions")}</summary>
        <label>
          {t("note")}
          <textarea
            aria-label={t("identityNotes")}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
          />
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          {t("confirmArrayGrouping")}
        </label>
      </details>
      <p className="fine-print">{t("verifiedIdentityRequirement")}</p>
      <button
        className="primary"
        onClick={() =>
          onSave({
            state,
            evidence_type: source,
            evidence,
            note,
            group_confirmed: confirmed,
          })
        }
      >
        {t("saveIdentity")}
      </button>
    </fieldset>
  );
}
