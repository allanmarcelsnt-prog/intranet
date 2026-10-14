import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Plus, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { must, useInvalidate, useMe } from "@/hooks/use-safenet";
import { ACERVO_LABEL, PRIORIDADES, acervoInsert, acervoResolvePatch, parseAcervo, type AcervoKind } from "@/lib/acervo";
import { formatBR, toISODate, type EventRow } from "@/lib/safenet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

/** Pontos de atenção e anormalidades técnicas do cliente (persistem entre semanas até serem resolvidos). */
export function AcervoPanel({ clientId }: { clientId: string }) {
  const { data: me } = useMe();
  const inv = useInvalidate();
  const [showResolved, setShowResolved] = useState(false);
  const [kind, setKind] = useState<AcervoKind>("anormalidade");
  const [date, setDate] = useState(() => toISODate(new Date()));
  const [text, setText] = useState("");
  const [who, setWho] = useState("");
  const [prio, setPrio] = useState("");
  const [resolving, setResolving] = useState<string | null>(null);
  const [solution, setSolution] = useState("");

  const { data: rows = [] } = useQuery({
    queryKey: ["acervo", clientId],
    queryFn: () => must<EventRow[]>(supabase.from("client_events").select("*").eq("client_id", clientId).eq("kind", "observation").order("event_date")),
  });
  const entries = rows.flatMap((r) => { const a = parseAcervo(r); return a ? [{ a, r }] : []; });
  const shown = entries.filter(({ a }) => showResolved || a.open);
  const refresh = () => inv("acervo", "events");

  const add = async () => {
    if (!text.trim()) { toast.error("Descreva o registro"); return; }
    const { error } = await supabase.from("client_events").insert(acervoInsert({
      clientId, kind, date, text, responsavel: who || me?.profile.name || "", prioridade: kind === "ponto" ? prio : "", operatorId: me?.id ?? null,
    }));
    if (error) { toast.error(error.message); return; }
    setText(""); setPrio(""); refresh(); toast.success("Registrado");
  };
  const patch = async (r: EventRow, resolve: boolean) => {
    const { error } = await supabase.from("client_events").update(acervoResolvePatch(r, resolve, solution, toISODate(new Date()))).eq("id", r.id);
    if (error) { toast.error(error.message); return; }
    setResolving(null); setSolution(""); refresh();
  };
  const remove = async (id: string) => {
    if (!window.confirm("Excluir este registro definitivamente?")) return;
    const { error } = await supabase.from("client_events").delete().eq("id", id);
    if (error) toast.error(error.message); else refresh();
  };

  return (
    <div className="space-y-3">
      <div className="no-print flex flex-wrap items-end gap-2">
        <Select value={kind} onValueChange={(v) => setKind(v as AcervoKind)}>
          <SelectTrigger className="w-48" aria-label="Tipo"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="anormalidade">{ACERVO_LABEL.anormalidade}</SelectItem>
            <SelectItem value="ponto">{ACERVO_LABEL.ponto}</SelectItem>
          </SelectContent>
        </Select>
        <Input type="date" className="w-40" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Data de identificação" />
        {kind === "ponto" && (
          <Select value={prio} onValueChange={setPrio}>
            <SelectTrigger className="w-32" aria-label="Prioridade"><SelectValue placeholder="Prioridade" /></SelectTrigger>
            <SelectContent>{PRIORIDADES.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}</SelectContent>
          </Select>
        )}
        <Input className="w-44" maxLength={120} placeholder={`Responsável (${me?.profile.name ?? "você"})`} value={who} onChange={(e) => setWho(e.target.value)} />
        <Input className="min-w-56 flex-1" maxLength={950} placeholder="Descrição" value={text} onChange={(e) => setText(e.target.value)} />
        <Button size="icon" onClick={add} aria-label="Adicionar"><Plus className="h-4 w-4" /></Button>
      </div>
      <label className="no-print flex items-center gap-2 text-sm"><Switch checked={showResolved} onCheckedChange={setShowResolved} />Mostrar resolvidos</label>
      {!shown.length && <p className="py-2 text-sm text-muted-foreground">Nenhum registro em aberto.</p>}
      <ul className="space-y-2">
        {shown.map(({ a, r }) => (
          <li key={a.id} className="rounded-md border p-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded bg-muted px-2 py-0.5 text-xs font-semibold">{ACERVO_LABEL[a.kind]}</span>
              <span className="font-mono text-xs text-muted-foreground">{formatBR(a.date)}</span>
              <span className={`rounded px-2 py-0.5 text-xs ${a.open ? "bg-warning/20" : "bg-success/20"}`}>{a.status}</span>
              {a.prioridade && <span className="text-xs text-muted-foreground">prioridade {a.prioridade}</span>}
              {a.responsavel && <span className="text-xs text-muted-foreground">resp.: {a.responsavel}</span>}
              <span className="no-print ml-auto flex gap-1">
                {a.open
                  ? <Button size="sm" variant="outline" onClick={() => { setResolving(resolving === a.id ? null : a.id); setSolution(""); }}><Check className="mr-1 h-3.5 w-3.5" />Resolver</Button>
                  : <Button size="sm" variant="ghost" onClick={() => patch(r, false)}><RotateCcw className="mr-1 h-3.5 w-3.5" />Reabrir</Button>}
                {me?.isAdmin && <Button size="icon" variant="ghost" aria-label="Excluir" onClick={() => remove(a.id)}><Trash2 className="h-4 w-4" /></Button>}
              </span>
            </div>
            <p className="mt-1 whitespace-pre-wrap">{a.text}</p>
            {!a.open && (a.solucao || a.resolvidoEm) && <p className="mt-1 text-xs text-muted-foreground">Solução: {a.solucao || "—"}{a.resolvidoEm ? ` (${formatBR(a.resolvidoEm)})` : ""}</p>}
            {resolving === a.id && (
              <div className="no-print mt-2 flex gap-2">
                <Input autoFocus maxLength={500} placeholder="Solução aplicada" value={solution} onChange={(e) => setSolution(e.target.value)} />
                <Button size="sm" onClick={() => patch(r, true)}>Confirmar</Button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
