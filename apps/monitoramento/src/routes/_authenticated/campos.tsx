import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { Plus, Trash2 } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";
import { FIELD_SCOPES, useCustomFields, useInvalidate, useMe, type CustomField } from "@/hooks/use-safenet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export const Route = createFileRoute("/_authenticated/campos")({
  head: () => ({
    meta: [
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { title: "Campos personalizados — Monitoramento Safenet" },
      { name: "description", content: "Crie, renomeie, altere e desative campos de preenchimento do plantão e do relatório." },
      { property: "og:title", content: "Campos personalizados — Monitoramento Safenet" },
      { property: "og:description", content: "Configuração dos campos de preenchimento da equipe." },
    ],
  }),
  component: Campos,
});

const TYPES: Record<string, string> = { text: "Texto", number: "Número", select: "Lista de opções" };

function Campos() {
  const { data: me } = useMe();
  const { data: fields = [] } = useCustomFields(undefined, true);
  const can = !!me?.canManage;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Campos personalizados</h1>
      <p className="text-sm text-muted-foreground">Campos extras aparecem nos formulários do Plantão e do Relatório. Desativar um campo o esconde dos formulários sem apagar o que já foi preenchido.</p>
      {!can && <p className="rounded-md border bg-muted/50 p-3 text-sm text-muted-foreground">Somente consulta. É necessária a permissão de cadastros.</p>}
      {Object.entries(FIELD_SCOPES).map(([scope, label]) => (
        <Card key={scope}>
          <CardHeader className="pb-2"><CardTitle className="text-base">{label}</CardTitle></CardHeader>
          <CardContent className="space-y-1.5">
            {fields.filter((f) => f.scope === scope).map((f) => <FieldRow key={f.id} f={f} can={can} isAdmin={!!me?.isAdmin} />)}
            {can && <NewField scope={scope} position={fields.length} />}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function useSave() {
  const inv = useInvalidate();
  return async (id: string, patch: Partial<CustomField>) => {
    const { error } = await supabase.from("custom_fields").update(patch).eq("id", id);
    if (error) toast.error(error.message); else inv("custom-fields");
  };
}

const parseOpts = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean).slice(0, 50);

function FieldRow({ f, can, isAdmin }: { f: CustomField; can: boolean; isAdmin: boolean }) {
  const save = useSave();
  const inv = useInvalidate();
  const del = async () => {
    if (!confirm(`Excluir o campo "${f.label}"? Prefira desativar para manter a referência nos registros antigos.`)) return;
    const { error } = await supabase.from("custom_fields").delete().eq("id", f.id);
    if (error) toast.error(error.message); else inv("custom-fields");
  };
  return (
    <div className={`flex flex-wrap items-center gap-2 ${f.active ? "" : "opacity-60"}`}>
      <Input disabled={!can} className="h-8 w-56" defaultValue={f.label} onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== f.label && save(f.id, { label: e.target.value.trim().slice(0, 80) })} aria-label="Nome do campo" />
      <Select disabled={!can} value={f.field_type} onValueChange={(v) => save(f.id, { field_type: v })}>
        <SelectTrigger className="h-8 w-40"><SelectValue /></SelectTrigger>
        <SelectContent>{Object.entries(TYPES).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}</SelectContent>
      </Select>
      {f.field_type === "select" && <Input disabled={!can} className="h-8 min-w-48 flex-1" placeholder="Opções separadas por vírgula" defaultValue={f.options.join(", ")} onBlur={(e) => save(f.id, { options: parseOpts(e.target.value) })} />}
      <label className="flex items-center gap-1.5 text-xs"><Switch disabled={!can} checked={f.active} onCheckedChange={(v) => save(f.id, { active: v })} />ativo</label>
      {isAdmin && <Button size="icon" variant="ghost" className="h-8 w-8" onClick={del} aria-label="Excluir"><Trash2 className="h-4 w-4" /></Button>}
    </div>
  );
}

function NewField({ scope, position }: { scope: string; position: number }) {
  const inv = useInvalidate();
  const [label, setLabel] = useState("");
  const [type, setType] = useState("text");
  const [opts, setOpts] = useState("");
  const add = async () => {
    const l = label.trim().slice(0, 80);
    if (!l) { toast.error("Informe o nome do campo"); return; }
    if (type === "select" && parseOpts(opts).length === 0) { toast.error("Informe as opções"); return; }
    const { error } = await supabase.from("custom_fields").insert({ scope, label: l, field_type: type, options: type === "select" ? parseOpts(opts) : [], position });
    if (error) { toast.error(error.message); return; }
    setLabel(""); setOpts(""); inv("custom-fields");
  };
  return (
    <div className="flex flex-wrap items-center gap-2 border-t pt-2">
      <Input className="h-8 w-56" placeholder="Novo campo" value={label} onChange={(e) => setLabel(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
      <Select value={type} onValueChange={setType}>
        <SelectTrigger className="h-8 w-40"><SelectValue /></SelectTrigger>
        <SelectContent>{Object.entries(TYPES).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}</SelectContent>
      </Select>
      {type === "select" && <Input className="h-8 min-w-48 flex-1" placeholder="Opções separadas por vírgula" value={opts} onChange={(e) => setOpts(e.target.value)} />}
      <Button size="icon" className="h-8 w-8" onClick={add} aria-label="Adicionar campo"><Plus className="h-4 w-4" /></Button>
    </div>
  );
}
