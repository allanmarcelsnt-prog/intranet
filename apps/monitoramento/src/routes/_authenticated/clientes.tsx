import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus, Trash2 } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";
import { useClientData, useClients, useInvalidate, useMe } from "@/hooks/use-safenet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const Route = createFileRoute("/_authenticated/clientes")({
  head: () => ({
    meta: [
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { title: "Clientes — Monitoramento Safenet" },
      { name: "description", content: "Cadastro de clientes monitorados, usuários autorizados, dispositivos e sensores." },
      { property: "og:title", content: "Clientes — Monitoramento Safenet" },
      { property: "og:description", content: "Cadastro de contas monitoradas pela Safenet." },
    ],
  }),
  component: Clientes,
});

type Table = "client_users" | "client_devices" | "client_sensors";

function Clientes() {
  const { data: clients = [] } = useClients();
  const { data: me } = useMe();
  const inv = useInvalidate();
  const [sel, setSel] = useState<string>();
  const [name, setName] = useState("");
  useEffect(() => { if (!sel && clients[0]) setSel(clients[0].id); }, [clients, sel]);
  const canManage = !!me?.canManage;

  const add = async () => {
    const n = name.trim().slice(0, 120);
    if (!n) return;
    const { data, error } = await supabase.from("clients").insert({ name: n }).select().single();
    if (error) { toast.error(error.message); return; }
    setName(""); inv("clients"); setSel(data.id);
  };
  const client = clients.find((c) => c.id === sel);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Clientes</h1>
      {!canManage && <p className="rounded-md border bg-muted/50 p-3 text-sm text-muted-foreground">Somente consulta. Peça a um administrador a permissão de cadastros para criar ou alterar clientes, dispositivos e sensores.</p>}
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <Card>
          <CardContent className="space-y-2 p-3">
            {canManage && <div className="flex gap-2"><Input placeholder="Nova empresa" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} /><Button size="icon" onClick={add} aria-label="Adicionar"><Plus className="h-4 w-4" /></Button></div>}
            {clients.map((c) => (
              <button key={c.id} onClick={() => setSel(c.id)} className={`w-full rounded-md px-3 py-2 text-left text-sm ${c.id === sel ? "bg-primary text-primary-foreground" : "hover:bg-accent"}`}>{c.name}</button>
            ))}
          </CardContent>
        </Card>
        {client && <ClientDetail key={client.id} id={client.id} name={client.name} address={client.address ?? ""} canManage={canManage} isAdmin={!!me?.isAdmin} />}
      </div>
    </div>
  );
}

function ClientDetail({ id, name, address, canManage, isAdmin }: { id: string; name: string; address: string; canManage: boolean; isAdmin: boolean }) {
  const { data } = useClientData(id);
  const inv = useInvalidate();
  const save = async (patch: { name?: string; address?: string }) => {
    const { error } = await supabase.from("clients").update(patch).eq("id", id);
    if (error) toast.error(error.message); else inv("clients");
  };
  const delClient = async () => {
    if (!confirm(`Remover ${name} da lista? O histórico de registros é mantido.`)) return;
    const { error } = await supabase.from("clients").update({ active: false }).eq("id", id);
    if (error) toast.error(error.message); else inv("clients");
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-wrap gap-2 p-4">
          <Input disabled={!canManage} className="flex-1 text-lg font-semibold" defaultValue={name} onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== name && save({ name: e.target.value.trim() })} />
          <Input disabled={!canManage} className="flex-1" placeholder="Endereço" defaultValue={address} onBlur={(e) => e.target.value !== address && save({ address: e.target.value })} />
          {isAdmin && <Button variant="ghost" onClick={delClient}><Trash2 className="mr-1 h-4 w-4" />Excluir</Button>}
        </CardContent>
      </Card>
      <SubList title="Usuários autorizados (armam/desarmam)" table="client_users" clientId={id} rows={data?.users ?? []} canManage={canManage} isAdmin={isAdmin}
        fields={[{ key: "name", ph: "Nome" }, { key: "role", ph: "Cargo / função" }]} />
      <SubList title="Dispositivos de arme e desarme" table="client_devices" clientId={id} rows={data?.devices ?? []} canManage={canManage} isAdmin={isAdmin}
        fields={[{ key: "name", ph: "Identificação (ex: Teclado recepção)" }, { key: "type", ph: "Tipo", options: ["Teclado", "Aplicativo", "Controle remoto", "Chaveiro", "Tag/Cartão"] }]} />
      <SubList title="Sensores / zonas instalados" table="client_sensors" clientId={id} rows={data?.sensors ?? []} canManage={canManage} isAdmin={isAdmin}
        fields={[{ key: "zone", ph: "Zona", w: "w-20" }, { key: "name", ph: "Local (ex: Porta principal)" }, { key: "type", ph: "Tipo", options: ["Sensor infravermelho", "Sensor magnético", "Barreira", "Sensor de vibração", "Detector de fumaça", "Botão de pânico", "Sirene"] }]} />
    </div>
  );
}

interface Field { key: string; ph: string; w?: string; options?: string[] }

function SubList({ title, table, clientId, rows, fields, canManage, isAdmin }: { title: string; table: Table; clientId: string; rows: { id: string; [k: string]: any }[]; fields: Field[]; canManage: boolean; isAdmin: boolean }) {
  const inv = useInvalidate();
  const [vals, setVals] = useState<Record<string, string>>({});
  const listId = `${table}-opts`;
  const add = async () => {
    const row: Record<string, string> = { client_id: clientId };
    for (const f of fields) row[f.key] = (vals[f.key] ?? "").trim().slice(0, 120);
    if (!row[fields[0]!.key] || fields.some((f) => f.options && !row[f.key])) { toast.error("Preencha os campos"); return; }
    const { error } = await supabase.from(table).insert(row as never);
    if (error) { toast.error(error.message); return; }
    setVals({}); inv("client-data", "all-sensors");
  };
  const upd = async (rid: string, key: string, v: string, old: string) => {
    if (v === old) return;
    const { error } = await supabase.from(table).update({ [key]: v } as never).eq("id", rid);
    if (error) toast.error(error.message); else inv("client-data", "all-sensors");
  };
  const del = async (rid: string) => {
    if (!confirm("Remover da lista? Registros antigos continuam com o nome original.")) return;
    const { error } = await supabase.from(table).update({ active: false } as never).eq("id", rid);
    if (error) toast.error(error.message); else inv("client-data", "all-sensors");
  };

  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle className="text-base">{title} ({rows.length})</CardTitle></CardHeader>
      <CardContent className="space-y-1.5">
        {fields.map((f) => f.options && <datalist key={f.key} id={`${listId}-${f.key}`}>{f.options.map((o) => <option key={o} value={o} />)}</datalist>)}
        {rows.map((r) => (
          <div key={r.id} className="flex gap-2">
            {fields.map((f) => <Input key={f.key} disabled={!canManage} className={`h-8 ${f.w ?? "flex-1"}`} defaultValue={r[f.key] ?? ""} list={f.options ? `${listId}-${f.key}` : undefined} onBlur={(e) => upd(r.id, f.key, e.target.value, r[f.key] ?? "")} />)}
            {isAdmin && <Button size="icon" variant="ghost" className="h-8 w-8" onClick={() => del(r.id)} aria-label="Excluir"><Trash2 className="h-4 w-4" /></Button>}
          </div>
        ))}
        {canManage && (
          <div className="flex gap-2 border-t pt-2">
            {fields.map((f) => <Input key={f.key} className={`h-8 ${f.w ?? "flex-1"}`} placeholder={f.ph} list={f.options ? `${listId}-${f.key}` : undefined} value={vals[f.key] ?? ""} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} onKeyDown={(e) => e.key === "Enter" && add()} />)}
            <Button size="icon" className="h-8 w-8" onClick={add} aria-label="Adicionar"><Plus className="h-4 w-4" /></Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
