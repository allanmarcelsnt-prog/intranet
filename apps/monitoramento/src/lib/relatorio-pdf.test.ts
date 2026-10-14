import { describe, expect, it } from "vitest";
import { jsPDF } from "jspdf";
import { buildReport, renderReportPdf, weekLabel, type ReportBypass } from "./relatorio-pdf";
import type { EventRow } from "./safenet";

const ev = (o: Partial<EventRow>): EventRow => ({
  id: "x", client_id: "c", kind: "arm", event_date: "2026-08-10", event_time: "10:00", user_name: null, device_name: null, by_operator: false,
  sensor_id: null, sensor_ids: [], sensor_labels: null, end_time: null, custom: {}, description: null, status: null, operator_id: null, archived: false, created_at: "", ...o,
});
const days = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-15", "2026-08-16"];
const client = { name: "Cliente Teste", address: "Rua A, 1", notes: "[legado:2375] Código: 2375 | Responsável: Maria" };

describe("relatório PDF", () => {
  it("rotula a semana pela quinta-feira (regra ISO do app antigo)", () => {
    expect(weekLabel("2026-08-10")).toBe("Semana 02 / Agosto/2026");
    expect(weekLabel("2026-08-31")).toBe("Semana 01 / Setembro/2026"); // quinta = 03/09
    expect(weekLabel("2026-07-27")).toBe("Semana 05 / Julho/2026");
  });

  it("monta as seções e separa acervo, visitas, zonas e observações", () => {
    const events = [
      ev({ kind: "disarm", event_time: "08:00", user_name: "Ana", by_operator: true, device_name: "Teclado" }),
      ev({ kind: "arm", event_time: "18:00", user_name: "Bia", device_name: "Aplicativo" }),
      ev({ kind: "trigger", event_time: "01:37", end_time: "02:08", sensor_labels: "IVP - Salão", description: "falso positivo" }),
      ev({ kind: "maintenance", description: "Visita técnica — equipe: X" }),
      ev({ kind: "observation", description: "[Obs. semanal] tudo normal" }),
      ev({ kind: "observation", description: "Zona 01 (Magnético) — Fechada" }),
    ];
    const acervo = [
      ev({ kind: "observation", event_date: "2026-08-03", status: "Em aberto", description: "[Anormalidade técnica] sensor com defeito" }),
      ev({ kind: "observation", event_date: "2026-08-08", status: "Resolvido", description: "[Ponto de atenção] resolvido antes da semana" }),
      ev({ kind: "observation", event_date: "2026-08-11", status: "Resolvido", description: "[Ponto de atenção] resolvido na semana", user_name: "Lázaro", custom: { _acervo: { prioridade: "Alta", solucao: "trocado", resolvido_em: "2026-08-12" } } }),
    ];
    const bypasses: ReportBypass[] = [{ sensor_id: null, sensor_label: "Zona 25", reason: "aberta no arme", start_date: "2026-08-10", end_date: null }];
    const r = buildReport({ client, days, events, bypasses, acervo, sensorName: () => "" });
    expect(r.code).toBe("2375");
    expect(r.responsible).toBe("Maria");
    expect(r.desarmes[0]?.user).toBe("Ana (operador)");
    expect(r.disparos[0]?.time).toBe("01:37 às 02:08");
    expect(r.zonas.map((z) => z.end)).toContain("segue inibida");
    expect(r.visitas).toHaveLength(1);
    expect(r.observacoes).toBe("tudo normal");
    expect(r.anormalidades).toHaveLength(1);
    expect(r.pontos.map((p) => p.text)).toEqual(["resolvido na semana"]);
    expect(r.pontos[0]).toMatchObject({ prioridade: "Alta", responsavel: "Lázaro", solucao: "trocado", resolvedInPeriod: true });
    expect(r.resumo.anormalidadesAbertas).toBe(1);

    const doc = new jsPDF({ unit: "mm", format: "a4" });
    renderReportPdf(doc, r, null);
    expect(doc.getNumberOfPages()).toBeGreaterThanOrEqual(9);
  });

  it("semana vazia ainda gera as 9 páginas", () => {
    const doc = new jsPDF({ unit: "mm", format: "a4" });
    renderReportPdf(doc, buildReport({ client, days, events: [], bypasses: [], acervo: [], sensorName: () => "" }), null);
    expect(doc.getNumberOfPages()).toBe(9);
  });
});
