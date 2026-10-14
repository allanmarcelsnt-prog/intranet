import { describe, it, expect } from "vitest";
import { weekDays, buildClientBlock, nextShiftType, timeRange, type EventRow } from "./safenet";

const ev = (p: Partial<EventRow>): EventRow => ({
  id: "1", client_id: "c", kind: "arm", event_date: "2026-10-06", event_time: null, user_name: null,
  device_name: null, by_operator: false, sensor_id: null, sensor_ids: [], sensor_labels: null, end_time: null, custom: {},
  description: null, status: null, operator_id: null, archived: false, created_at: "", ...p,
});

describe("semana", () => {
  it("vai de segunda a domingo", () => {
    const d = weekDays(new Date(2026, 9, 8)); // quinta 08/10/2026
    expect(d[0]).toBe("2026-10-05");
    expect(d[6]).toBe("2026-10-11");
  });
  it("domingo pertence à semana anterior iniciada na segunda", () => {
    expect(weekDays(new Date(2026, 9, 11))[0]).toBe("2026-10-05");
  });
});

const shift = { shiftType: "Diurno", operatorName: "Operador Lucas", nextShiftType: "Noturno", nextOperatorName: "operador Daniel" };

describe("mensagem de passagem de plantão", () => {
  it("segue o modelo do WhatsApp sem linhas em branco dentro dos blocos", () => {
    const msg = buildClientBlock(
      { clientName: "Empresa Cliente Teste 1", date: "2026-10-06", events: [ev({ kind: "disarm", event_time: "06:49:00", user_name: "Fulano" })], newBypasses: [], sensorLabel: () => "" },
      shift,
    );
    expect(msg).toBe(
      "(Empresa Cliente Teste 1)\n\nRelatório de Plantão:\n\nData: 06/10/2026\nPlantão Finalizado: sem alterações\nTurno: Diurno\nNome: Operador Lucas\nDesarme: 06:49 por Fulano - Arme:\nObservações: S/A\n\nPlantão Iniciado: sem alterações\nTurno: Noturno\nNome: operador Daniel",
    );
  });
  it("disparo contínuo com vários sensores aparece com intervalo", () => {
    const msg = buildClientBlock(
      { clientName: "X", date: "2026-10-06", events: [ev({ kind: "trigger", event_time: "14:10", end_time: "14:25", sensor_labels: "Z1 Porta, Z2 Janela" })], newBypasses: [], sensorLabel: () => "" },
      shift,
    );
    expect(msg).toContain("Plantão Finalizado: disparo 14:10 às 14:25 Z1 Porta, Z2 Janela");
  });
  it("intervalo de horário", () => {
    expect(timeRange({ event_time: "14:10:00", end_time: null })).toBe("14:10");
    expect(timeRange({ event_time: "14:10:00", end_time: "14:25:00" })).toBe("14:10 às 14:25");
  });
  it("alterna o turno", () => {
    expect(nextShiftType("Diurno")).toBe("Noturno");
    expect(nextShiftType("Noturno")).toBe("Diurno");
  });
});
