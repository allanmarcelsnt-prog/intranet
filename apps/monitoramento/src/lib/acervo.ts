// Acervo do cliente: pontos de atenção e anormalidades técnicas. Persistem entre as semanas até serem resolvidos.
// Guardados como eventos "observation" com um prefixo na descrição; responsável em user_name e os demais
// campos (prioridade, solução, data de resolução) em custom._acervo. Assim não precisam de tabela nova.
import type { Json } from "../integrations/supabase/types";
import type { EventRow } from "./safenet";

export const ACERVO_TAGS = { ponto: "[Ponto de atenção]", anormalidade: "[Anormalidade técnica]" } as const;
export type AcervoKind = keyof typeof ACERVO_TAGS;
export const ACERVO_LABEL: Record<AcervoKind, string> = { ponto: "Ponto de atenção", anormalidade: "Anormalidade técnica" };
export const STATUS_ABERTO = "Em aberto";
export const STATUS_RESOLVIDO = "Resolvido";
export const PRIORIDADES = ["Alta", "Média", "Baixa"] as const;

export interface AcervoMeta { prioridade?: string; solucao?: string; resolvido_em?: string }

export interface AcervoEntry {
  id: string; kind: AcervoKind; date: string; text: string; status: string; open: boolean;
  responsavel: string; prioridade: string; solucao: string; resolvidoEm: string;
}

export const isResolvedStatus = (s: string | null | undefined) => /resolvid|conclu|encerrad/i.test(s ?? "");

export function metaOf(e: Pick<EventRow, "custom">): AcervoMeta {
  const m = (e.custom as Record<string, unknown> | undefined)?.["_acervo"];
  return m && typeof m === "object" ? (m as AcervoMeta) : {};
}

export function parseAcervo(e: EventRow): AcervoEntry | null {
  if (e.kind !== "observation") return null;
  const d = e.description ?? "";
  const kind = (Object.keys(ACERVO_TAGS) as AcervoKind[]).find((k) => d.startsWith(ACERVO_TAGS[k]));
  if (!kind) return null;
  const m = metaOf(e);
  return {
    id: e.id, kind, date: e.event_date, text: d.slice(ACERVO_TAGS[kind].length).trim(), status: e.status ?? STATUS_ABERTO,
    open: !isResolvedStatus(e.status), responsavel: e.user_name ?? "", prioridade: m.prioridade ?? "", solucao: m.solucao ?? "",
    resolvidoEm: m.resolvido_em ?? "",
  };
}

/** Linha para inserir em client_events. */
export function acervoInsert(a: { clientId: string; kind: AcervoKind; date: string; text: string; responsavel: string; prioridade: string; operatorId: string | null }) {
  return {
    client_id: a.clientId, kind: "observation" as const, event_date: a.date, event_time: null,
    description: `${ACERVO_TAGS[a.kind]} ${a.text.trim().slice(0, 950)}`, status: STATUS_ABERTO,
    user_name: a.responsavel.trim().slice(0, 120) || null, operator_id: a.operatorId,
    custom: (a.prioridade ? { _acervo: { prioridade: a.prioridade } } : {}) as Json,
  };
}

/** Marca como resolvido (ou reabre) preservando os demais campos de custom. */
export function acervoResolvePatch(e: EventRow, resolve: boolean, solucao: string, today: string) {
  const custom = { ...(e.custom ?? {}) } as Record<string, unknown>;
  const meta: AcervoMeta = { ...metaOf(e) };
  if (resolve) { meta.solucao = solucao.trim().slice(0, 500); meta.resolvido_em = today; } else { delete meta.solucao; delete meta.resolvido_em; }
  custom["_acervo"] = meta;
  return { status: resolve ? STATUS_RESOLVIDO : STATUS_ABERTO, custom: custom as Json };
}
