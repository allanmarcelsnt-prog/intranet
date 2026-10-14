// Pure helpers for Monitoramento Safenet (dates, report building, CSV).

export type EventKind = "arm" | "disarm" | "trigger" | "maintenance" | "observation";

export interface EventRow {
  id: string;
  client_id: string;
  kind: EventKind;
  event_date: string; // yyyy-mm-dd
  event_time: string | null; // HH:MM[:SS]
  user_name: string | null;
  device_name: string | null;
  by_operator: boolean;
  sensor_id: string | null;
  sensor_ids: string[];
  sensor_labels: string | null;
  end_time: string | null;
  custom: Record<string, unknown>;
  description: string | null;
  status: string | null;
  operator_id: string | null;
  archived: boolean;
  created_at: string;
}

export const pad = (n: number) => String(n).padStart(2, "0");

/** Local date as yyyy-mm-dd. */
export function toISODate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function fromISODate(s: string): Date {
  const [y = 1970, m = 1, d = 1] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

/** dd/mm/yyyy */
export function formatBR(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}

export function nowTime(d = new Date()): string {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export const hhmm = (t: string | null | undefined) => (t ? t.slice(0, 5) : "");

/** Monday of the week containing `d`. */
export function weekStart(d: Date): Date {
  const r = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = r.getDay(); // 0 = Sunday
  const diff = day === 0 ? -6 : 1 - day;
  r.setDate(r.getDate() + diff);
  return r;
}

export function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
}

/** Monday..Sunday ISO dates for the week containing `d`. */
export function weekDays(d: Date): string[] {
  const s = weekStart(d);
  return Array.from({ length: 7 }, (_, i) => addDays(s, i))
    .filter(day => day.getMonth() === d.getMonth() && day.getFullYear() === d.getFullYear())
    .map(toISODate);
}

export function adjacentReportWeek(d: Date, direction: -1 | 1): Date {
  const days = weekDays(d);
  const edge = direction === -1 ? days[0] : days.at(-1);
  return edge ? addDays(fromISODate(edge), direction) : d;
}

export function weekdayLabel(iso: string): string {
  return WEEKDAY_LABELS[(fromISODate(iso).getDay() + 6) % 7] ?? "";
}

export const WEEKDAY_LABELS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];

export function monthRange(d: Date): { from: string; to: string } {
  const from = new Date(d.getFullYear(), d.getMonth(), 1);
  const to = new Date(d.getFullYear(), d.getMonth() + 1, 0);
  return { from: toISODate(from), to: toISODate(to) };
}

export const KIND_LABEL: Record<EventKind, string> = {
  arm: "Arme",
  disarm: "Desarme",
  trigger: "Disparo",
  maintenance: "Manutenção",
  observation: "Observação",
};

export function latest(events: EventRow[], kind: EventKind): EventRow | undefined {
  return events
    .filter((e) => e.kind === kind)
    .sort((a, b) => (b.event_date + (b.event_time ?? "")).localeCompare(a.event_date + (a.event_time ?? "")) || b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))[0];
}

export function isDisarmed(events: EventRow[]): boolean {
  return latestArmState(events)?.kind === "disarm";
}

function latestArmState(events: EventRow[]): EventRow | undefined {
  return [...events].filter(e => e.kind === "arm" || e.kind === "disarm")
    .sort((a, b) => (b.event_date + (b.event_time ?? "")).localeCompare(a.event_date + (a.event_time ?? "")) || b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))[0];
}

export function shiftContainsEvent(e: EventRow, startedAt: string, endedAt: Date): boolean {
  const occurredAt = e.kind === 'arm' || e.kind === 'disarm'
    ? new Date(`${e.event_date}T${e.event_time || "00:00:00"}`).getTime()
    : new Date(e.created_at).getTime();
  return occurredAt >= new Date(startedAt).getTime() && occurredAt <= endedAt.getTime();
}

export function handoverRecords(events: EventRow[], previous: EventRow[] = []) {
  const disarm = latest(events, "disarm");
  const arm = latest(events, "arm");
  return {
    disarm: disarm ?? latest(previous, "disarm"),
    arm: arm ?? (disarm ? undefined : latest(previous, "arm")),
    previousDisarm: !disarm,
    previousArm: !arm,
  };
}

function previousArmText(e: EventRow | undefined): string {
  if (!e) return "";
  return `${formatBR(e.event_date)} ${armText(e)} — dispositivo: ${e.device_name || "não informado"} (registro anterior)`;
}

function armText(e: EventRow | undefined): string {
  if (!e) return "";
  const who = e.by_operator ? `${e.user_name ?? "operador"} (operador)` : e.user_name ?? "—";
  return `${hhmm(e.event_time)} por ${who}`;
}

export interface ShiftClientInput {
  clientName: string;
  date: string; // ISO
  events: EventRow[]; // events of the shift for this client
  previousEvents?: EventRow[]; // read-only context before this shift; never copied into events
  newBypasses: { sensorLabel: string; reason: string | null }[];
  sensorLabel: (id: string | null) => string;
}

export interface ShiftInfo {
  shiftType: string;
  operatorName: string;
  nextShiftType: string;
  nextOperatorName: string;
}

/** "06:49" or "06:49 às 07:10" for continuous events. */
export function timeRange(e: Pick<EventRow, "event_time" | "end_time">): string {
  const a = hhmm(e.event_time);
  const b = hhmm(e.end_time);
  return b ? `${a} às ${b}` : a;
}

/** Sensor names of an event: stored snapshot first, live lookup as fallback. */
export function eventSensors(e: EventRow, sensorLabel: (id: string | null) => string): string {
  if (e.sensor_labels) return e.sensor_labels;
  const ids = e.sensor_ids?.length ? e.sensor_ids : e.sensor_id ? [e.sensor_id] : [];
  return ids.map((id) => sensorLabel(id)).filter(Boolean).join(", ");
}

/** Builds the WhatsApp block for one client. */
export function buildClientBlock(c: ShiftClientInput, s: ShiftInfo): string {
  const records = handoverRecords(c.events, c.previousEvents);
  const alterations: string[] = [];
  for (const e of c.events.filter((x) => x.kind === "trigger")) {
    alterations.push(
      `disparo ${timeRange(e)} ${eventSensors(e, c.sensorLabel)}${e.description ? ` (${e.description})` : ""}`.trim(),
    );
  }
  for (const b of c.newBypasses) {
    alterations.push(`zona inibida ${b.sensorLabel}${b.reason ? ` (${b.reason})` : ""}`);
  }
  for (const e of c.events.filter((x) => x.kind === "maintenance")) {
    alterations.push(`manutenção: ${e.description ?? ""}`.trim());
  }
  const altText = alterations.length ? alterations.join("; ") : "sem alterações";
  const obs = c.events
    .filter((e) => e.kind === "observation" && e.description)
    .map((e) => e.description)
    .join("; ");

  return [
    `(${c.clientName})`,
    "",
    "Relatório de Plantão:",
    "",
    `Data: ${formatBR(c.date)}`,
    `Plantão Finalizado: ${altText}`,
    `Turno: ${s.shiftType}`,
    `Nome: ${s.operatorName}`,
    `Desarme: ${records.previousDisarm ? previousArmText(records.disarm) : armText(records.disarm)} - Arme: ${records.previousArm ? previousArmText(records.arm) : armText(records.arm)}`.trimEnd(),
    `Observações: ${obs || "S/A"}`,
    "",
    `Plantão Iniciado: sem alterações`,
    `Turno: ${s.nextShiftType}`,
    `Nome: ${s.nextOperatorName}`,
  ].join("\n");
}

export function buildShiftMessage(clients: ShiftClientInput[], s: ShiftInfo): string {
  return clients.map((c) => buildClientBlock(c, s)).join("\n\n────────────\n\n");
}

export const nextShiftType = (t: string) => (t === "Diurno" ? "Noturno" : "Diurno");

function csvCell(v: unknown): string {
  const s = v === null || v === undefined ? "" : String(v);
  return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCSV(rows: Record<string, unknown>[], columns: string[]): string {
  return [columns.join(","), ...rows.map((r) => columns.map((c) => csvCell(r[c])).join(","))].join("\n");
}

export function downloadText(filename: string, text: string, mime = "text/csv;charset=utf-8") {
  const blob = new Blob(["\ufeff" + text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
