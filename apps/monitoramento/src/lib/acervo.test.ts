import { describe, expect, it } from "vitest";
import { acervoInsert, acervoResolvePatch, parseAcervo } from "./acervo";
import type { EventRow } from "./safenet";

const row = (o: Partial<EventRow>): EventRow => ({
  id: "1", client_id: "c", kind: "observation", event_date: "2026-08-03", event_time: null, user_name: null, device_name: null, by_operator: false,
  sensor_id: null, sensor_ids: [], sensor_labels: null, end_time: null, custom: {}, description: null, status: null, operator_id: null, archived: false, created_at: "", ...o,
});

describe("acervo", () => {
  it("ignora observações comuns e reconhece os dois tipos", () => {
    expect(parseAcervo(row({ description: "teste" }))).toBeNull();
    expect(parseAcervo(row({ kind: "arm", description: "[Ponto de atenção] x" }))).toBeNull();
    expect(parseAcervo(row({ description: "[Ponto de atenção] porta emperra", status: "Em aberto" }))).toMatchObject({ kind: "ponto", text: "porta emperra", open: true });
    expect(parseAcervo(row({ description: "[Anormalidade técnica] câmera off", status: "Resolvido" }))).toMatchObject({ kind: "anormalidade", open: false });
  });

  it("insere em aberto, com responsável e prioridade", () => {
    const i = acervoInsert({ clientId: "c", kind: "ponto", date: "2026-10-06", text: "  texto ", responsavel: "Ana", prioridade: "Alta", operatorId: "u" });
    expect(i).toMatchObject({ kind: "observation", status: "Em aberto", user_name: "Ana", description: "[Ponto de atenção] texto", custom: { _acervo: { prioridade: "Alta" } } });
  });

  it("resolver e reabrir preservam prioridade e demais campos", () => {
    const e = row({ description: "[Ponto de atenção] x", status: "Em aberto", custom: { outro: 1, _acervo: { prioridade: "Alta" } } });
    const res = acervoResolvePatch(e, true, "trocado o sensor", "2026-10-06");
    expect(res.status).toBe("Resolvido");
    expect(res.custom).toMatchObject({ outro: 1, _acervo: { prioridade: "Alta", solucao: "trocado o sensor", resolvido_em: "2026-10-06" } });
    const back = acervoResolvePatch(row({ custom: res.custom as Record<string, unknown> }), false, "", "2026-10-07");
    expect(back.status).toBe("Em aberto");
    expect(back.custom).toMatchObject({ outro: 1, _acervo: { prioridade: "Alta" } });
    expect(JSON.stringify(back.custom)).not.toContain("solucao");
  });
});
