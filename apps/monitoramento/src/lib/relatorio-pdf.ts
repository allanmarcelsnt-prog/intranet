// Relatório semanal em PDF (formato do app antigo): capa, dados e resumo, desarmes, armes, disparos,
// zonas, visitas/manutenções, pontos de atenção e anormalidades técnicas + observações.
// A montagem dos dados (buildReport) é pura e testável; o desenho (renderReportPdf) usa jsPDF.
import type { jsPDF } from "jspdf";
import { fromISODate, formatBR, hhmm, timeRange, eventSensors, type EventRow } from "./safenet";
import { ACERVO_TAGS, isResolvedStatus, parseAcervo, type AcervoKind } from "./acervo";

const TAG_PONTO = ACERVO_TAGS.ponto;
const TAG_ANORMALIDADE = ACERVO_TAGS.anormalidade;
export const TAG_OBS_SEMANAL = "[Obs. semanal]";
export const TAG_VISITA = "Visita técnica";

export interface ReportClient { name: string; address: string | null; notes: string | null }
export interface ReportBypass { sensor_id: string | null; sensor_label: string | null; reason: string | null; start_date: string; end_date: string | null }

export interface ReportModel {
  clientName: string;
  code: string;
  address: string;
  responsible: string;
  from: string;
  to: string;
  weekLabel: string;
  armes: { date: string; time: string; user: string; device: string }[];
  desarmes: { date: string; time: string; user: string; device: string }[];
  disparos: { date: string; time: string; sensors: string; note: string }[];
  zonas: { start: string; end: string; zone: string; reason: string }[];
  visitas: { date: string; text: string }[];
  pontos: AcervoItem[];
  anormalidades: AcervoItem[];
  observacoes: string;
  resumo: { armes: number; desarmes: number; disparos: number; zonas: number; visitas: number; pontosAbertos: number; anormalidadesAbertas: number };
}

export interface AcervoItem { date: string; status: string; text: string; resolvedInPeriod: boolean; prioridade: string; responsavel: string; solucao: string; resolvidoEm: string }

const MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];

/** Regra ISO do app antigo: a semana pertence ao mês da sua quinta-feira. */
export function weekLabel(mondayISO: string): string {
  const thu = fromISODate(mondayISO);
  thu.setDate(thu.getDate() + 3);
  const n = Math.floor((thu.getDate() - 1) / 7) + 1;
  return `Semana ${String(n).padStart(2, "0")} / ${MESES[thu.getMonth()]}/${thu.getFullYear()}`;
}

const byDateTime = (a: { event_date: string; event_time: string | null }, b: { event_date: string; event_time: string | null }) =>
  a.event_date === b.event_date ? hhmm(a.event_time).localeCompare(hhmm(b.event_time)) : a.event_date.localeCompare(b.event_date);

const stripTag = (text: string, tag: string) => text.replace(tag, "").trim();
const isResolved = isResolvedStatus;

export function buildReport(input: {
  client: ReportClient;
  days: string[]; // 7 datas ISO (segunda a domingo)
  events: EventRow[]; // eventos da semana
  bypasses: ReportBypass[]; // zonas que tocam a semana
  acervo: EventRow[]; // observações [Ponto de atenção]/[Anormalidade técnica] do cliente (qualquer data)
  sensorName: (id: string | null) => string;
}): ReportModel {
  const { client, days, events, bypasses, acervo, sensorName } = input;
  const from = days[0] ?? "", to = days[6] ?? "";
  const code = /Código:\s*([^|]+?)(?:\s*\||$)/.exec(client.notes ?? "")?.[1]?.trim() ?? "-";
  const responsible = /Responsável:\s*(.+)$/m.exec(client.notes ?? "")?.[1]?.trim() ?? "-";
  const sorted = [...events].sort(byDateTime);
  const arm = (kind: "arm" | "disarm") => sorted.filter((e) => e.kind === kind).map((e) => ({
    date: e.event_date, time: hhmm(e.event_time), user: (e.user_name ?? "-") + (e.by_operator ? " (operador)" : ""), device: e.device_name ?? "-",
  }));
  const disparos = sorted.filter((e) => e.kind === "trigger").map((e) => ({
    date: e.event_date, time: timeRange(e), sensors: eventSensors(e, sensorName) || "-", note: e.description ?? "",
  }));
  const zonaObs = sorted.filter((e) => e.kind === "observation" && /^Zona /.test(e.description ?? ""));
  const zonas = [
    ...bypasses.map((b) => ({
      start: b.start_date, end: b.end_date ?? "segue inibida", zone: b.sensor_label ?? sensorName(b.sensor_id), reason: b.reason ?? "",
    })),
    ...zonaObs.map((e) => ({ start: e.event_date, end: "", zone: "", reason: e.description ?? "" })),
  ].sort((a, b) => a.start.localeCompare(b.start));
  const visitas = sorted.filter((e) => e.kind === "maintenance").map((e) => ({ date: e.event_date, text: e.description ?? "" }));

  // Acervo persiste entre semanas: entra o que já foi identificado até o fim da semana e ainda está em aberto,
  // mais o que foi identificado ou resolvido dentro do período.
  const pick = (kind: AcervoKind): AcervoItem[] => acervo
    .flatMap((e) => { const a = parseAcervo(e); return a && a.kind === kind && a.date <= to ? [a] : []; })
    .map((a): AcervoItem => ({
      date: a.date, status: a.status, text: a.text, prioridade: a.prioridade, responsavel: a.responsavel, solucao: a.solucao, resolvidoEm: a.resolvidoEm,
      resolvedInPeriod: !a.open && ((a.resolvidoEm || a.date) >= from && (a.resolvidoEm || a.date) <= to),
    }))
    .filter((i) => !isResolved(i.status) || i.resolvedInPeriod || (i.date >= from && i.date <= to))
    .sort((x, y) => x.date.localeCompare(y.date));
  const pontos = pick("ponto"), anormalidades = pick("anormalidade");

  const observacoes = sorted
    .filter((e) => e.kind === "observation" && !/^Zona /.test(e.description ?? "") && !(e.description ?? "").startsWith(TAG_PONTO) && !(e.description ?? "").startsWith(TAG_ANORMALIDADE))
    .map((e) => stripTag(e.description ?? "", TAG_OBS_SEMANAL))
    .filter(Boolean).join("\n");

  const isVisita = (t: string) => t.startsWith(TAG_VISITA);
  return {
    clientName: client.name, code, address: client.address ?? "-", responsible, from, to, weekLabel: weekLabel(from),
    armes: arm("arm"), desarmes: arm("disarm"), disparos, zonas, visitas, pontos, anormalidades, observacoes,
    resumo: {
      armes: arm("arm").length, desarmes: arm("disarm").length, disparos: disparos.length, zonas: zonas.length,
      visitas: visitas.filter((v) => isVisita(v.text)).length || visitas.length,
      pontosAbertos: pontos.filter((p) => !isResolved(p.status)).length,
      anormalidadesAbertas: anormalidades.filter((p) => !isResolved(p.status)).length,
    },
  };
}

// ---------- desenho ----------
const M = 20, W = 170, BOTTOM = 275;

export function renderReportPdf(doc: jsPDF, r: ReportModel, logo: string | null): void {
  const logoH = (w: number) => w * (186 / 882);
  const put = (img: string | null, x: number, y: number, w: number) => { if (img) doc.addImage(img, "PNG", x, y, w, logoH(w)); };
  const text = (s: string, x: number, y: number, o: { size?: number; bold?: boolean; align?: "center" | "left" | "right"; max?: number } = {}) => {
    doc.setFont("helvetica", o.bold ? "bold" : "normal"); doc.setFontSize(o.size ?? 10);
    doc.text(s, x, y, { ...(o.align ? { align: o.align } : {}), ...(o.max ? { maxWidth: o.max } : {}) });
  };
  const wrap = (s: string, w: number, size = 10, bold = false) => { doc.setFont("helvetica", bold ? "bold" : "normal"); doc.setFontSize(size); return doc.splitTextToSize(s || "-", w) as string[]; };

  const header = (title: string): number => {
    put(logo, M, 10, 28);
    text("RELATÓRIO SEMANAL DE MONITORAMENTO", 112, 16, { size: 12, bold: true, align: "center" });
    text(r.weekLabel.toUpperCase(), 112, 22, { align: "center" });
    text(`Cliente: ${r.clientName}`, M, 31, { max: 100 });
    text(`Período: ${formatBR(r.from)} a ${formatBR(r.to)}`, 190, 31, { align: "right" });
    doc.setDrawColor(180); doc.line(M, 34, 190, 34);
    text(title, M, 42, { size: 12, bold: true });
    return 50;
  };
  let title = "";
  const newPage = (t: string) => { doc.addPage(); title = t; return header(t); };
  const ensure = (y: number, need: number) => (y + need > BOTTOM ? newPage(`${title} (continuação)`) : y);

  const table = (y: number, heads: string[], widths: number[], rows: string[][], empty: string): number => {
    const head = (yy: number) => {
      doc.setFillColor(235, 238, 245); doc.rect(M, yy - 4.5, W, 6.5, "F");
      let x = M; heads.forEach((h, i) => { text(h, x + 1, yy, { bold: true }); x += widths[i] ?? 0; });
      return yy + 7;
    };
    y = head(y);
    if (!rows.length) { for (const l of wrap(empty, W)) { text(l, M, y); y += 5; } return y; }
    for (const row of rows) {
      const cells = row.map((c, i) => wrap(c, (widths[i] ?? 30) - 2));
      const h = Math.max(...cells.map((c) => c.length)) * 4.6 + 2;
      if (y + h > BOTTOM) { y = newPage(`${title} (continuação)`); y = head(y); }
      let x = M;
      cells.forEach((c, i) => { doc.setFont("helvetica", "normal"); doc.setFontSize(10); c.forEach((l, k) => doc.text(l, x + 1, y + k * 4.6)); x += widths[i] ?? 0; });
      y += h; doc.setDrawColor(225); doc.line(M, y - 3.2, 190, y - 3.2);
    }
    return y;
  };
  const block = (y: number, head: string, body: string) => {
    const lines = wrap(body, W);
    y = ensure(y, 6 + Math.min(lines.length, 3) * 5);
    text(head, M, y, { bold: true }); y += 5;
    for (const l of lines) { y = ensure(y, 5); text(l, M, y); y += 4.8; }
    return y + 3;
  };
  const dt = (iso: string) => formatBR(iso);
  const acervoHead = (i: AcervoItem) => [dt(i.date), i.status + (i.resolvedInPeriod ? " (resolvido nesta semana)" : ""), i.prioridade && `prioridade ${i.prioridade}`, i.responsavel && `resp.: ${i.responsavel}`].filter(Boolean).join(" - ");
  const acervoBody = (i: AcervoItem) => i.text + (i.solucao ? `\nSolução: ${i.solucao}${i.resolvidoEm ? ` (${dt(i.resolvidoEm)})` : ""}` : "");

  // 1 — Capa
  title = "CAPA";
  put(logo, 55, 62, 100);
  text("RELATÓRIO SEMANAL DE MONITORAMENTO", 105, 112, { size: 20, bold: true, align: "center", max: 160 });
  text(r.clientName, 105, 132, { size: 14, align: "center", max: 160 });
  text(r.weekLabel, 105, 141, { size: 13, align: "center" });
  text(`${formatBR(r.from)} a ${formatBR(r.to)}`, 105, 149, { align: "center" });

  // 2 — Dados e resumo
  let y = newPage("DADOS DO CLIENTE E RESUMO");
  y = block(y, "Código", r.code); y = block(y, "Endereço", r.address); y = block(y, "Responsável", r.responsible);
  y += 2;
  const s = r.resumo;
  y = table(y, ["Item", "Quantidade"], [110, 60], [
    ["Desarmes", String(s.desarmes)], ["Armes", String(s.armes)], ["Disparos de alarme", String(s.disparos)],
    ["Zonas abertas/inibidas", String(s.zonas)], ["Visitas e manutenções", String(s.visitas)],
    ["Pontos de atenção em aberto", String(s.pontosAbertos)], ["Anormalidades técnicas em aberto", String(s.anormalidadesAbertas)],
  ], "-");

  // 3/4 — Desarmes e armes
  y = newPage("REGISTRO DE EVENTOS - DESARMES");
  table(y, ["Data", "Hora", "Usuário", "Dispositivo"], [28, 20, 72, 50], r.desarmes.map((e) => [dt(e.date), e.time, e.user, e.device]), "Nenhum desarme registrado na semana.");
  y = newPage("REGISTRO DE EVENTOS - ARMES");
  table(y, ["Data", "Hora", "Usuário", "Dispositivo"], [28, 20, 72, 50], r.armes.map((e) => [dt(e.date), e.time, e.user, e.device]), "Nenhum arme registrado na semana.");

  // 5 — Disparos
  y = newPage("DISPAROS DE ALARME");
  table(y, ["Data", "Hora", "Sensores", "Observação"], [24, 28, 62, 56], r.disparos.map((e) => [dt(e.date), e.time, e.sensors, e.note]), "Nenhum disparo registrado na semana.");

  // 6 — Zonas
  y = newPage("ZONAS ABERTAS/INIBIDAS");
  table(y, ["Início", "Fim", "Zona", "Motivo"], [24, 28, 56, 62], r.zonas.map((z) => [dt(z.start), z.end && /^\d{4}-/.test(z.end) ? dt(z.end) : z.end, z.zone, z.reason]), "Nenhuma zona registrada na semana.");

  // 7 — Visitas / manutenções
  y = newPage("VISITAS TÉCNICAS E MANUTENÇÕES");
  table(y, ["Data", "Descrição"], [28, 142], r.visitas.map((v) => [dt(v.date), v.text]), "Nenhuma visita técnica registrada na semana.");

  // 8 — Pontos de atenção
  y = newPage("PONTOS DE ATENÇÃO");
  if (!r.pontos.length) text("Nenhum ponto de atenção no período.", M, y);
  for (const p of r.pontos) y = block(y, acervoHead(p), acervoBody(p));

  // 9 — Anormalidades + observações
  y = newPage("ANORMALIDADES TÉCNICAS E OBSERVAÇÕES");
  if (!r.anormalidades.length) { text("Nenhuma anormalidade técnica identificada no período.", M, y); y += 8; }
  for (const a of r.anormalidades) y = block(y, acervoHead(a), acervoBody(a));
  y = ensure(y + 4, 14);
  y = block(y, "Observações gerais", r.observacoes || "-");

  // rodapé em todas as páginas (exceto capa)
  const total = doc.getNumberOfPages();
  for (let p = 2; p <= total; p++) {
    doc.setPage(p); doc.setFont("helvetica", "normal"); doc.setFontSize(8); doc.setTextColor(120);
    doc.text(`Página ${p} de ${total}`, 105, 288, { align: "center" }); doc.setTextColor(0);
  }
}
