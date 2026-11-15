export function readReviewer(): string {
  try {
    return localStorage.getItem("roof-pv-reviewer") || "";
  } catch {
    return "";
  }
}

export function writeReviewer(value: string): void {
  try {
    localStorage.setItem("roof-pv-reviewer", value);
  } catch {
    /* Preferences are optional; review state is server-side. */
  }
}
