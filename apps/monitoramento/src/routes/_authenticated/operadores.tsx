import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { useInvalidate, useMe, useOperators, must, type Shift } from "@/hooks/use-safenet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useServerFn } from '@tanstack/react-start';
import { removeOperator } from '@/lib/operators.functions';
import { canChangeOperator } from '@/lib/access';

export const Route = createFileRoute("/_authenticated/operadores")({
  head: () => ({
    meta: [
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { title: "Operadores — Monitoramento Safenet" },
      { name: "description", content: "Operadores de monitoramento cadastrados e histórico de plantões." },
      { property: "og:title", content: "Operadores — Monitoramento Safenet" },
      { property: "og:description", content: "Equipe de monitoramento e sessões de plantão." },
    ],
  }),
  component: Operadores,
});

function Operadores() {
  const { data: me } = useMe();
  const { data: ops = [] } = useOperators(true);
  const remove = useServerFn(removeOperator);
  const approve = async (id: string) => {
    const { error } = await supabase.rpc('set_operator_access', { _user_id: id, _status: 'approved' });
    if (error) { toast.error(error.message); return; }
    inv('operators'); toast.success('Operador autorizado');
  };
  const deleteOperator = async (id: string, name: string) => {
    if (!window.confirm(`Excluir o acesso de ${name}? O histórico dos plantões será preservado.`)) return;
    try { await remove({ data: { userId: id } }); toast.success('Cadastro removido e acesso bloqueado'); }
    catch (e) { toast.error(e instanceof Error ? e.message : 'Não foi possível remover'); }
    finally { inv('operators'); }
  };
  const inv = useInvalidate();
  const { data: shifts = [] } = useQuery({
    queryKey: ["shifts-history"],
    queryFn: () => must<Shift[]>(supabase.from("shifts").select("*").order("started_at", { ascending: false }).limit(30)),
  });
  const name = (id: string | null) => ops.find((o) => o.id === id)?.name ?? "—";
  const fmt = (s: string | null) => (s ? new Date(s).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "em andamento");

  const rename = async (v: string) => {
    if (!v.trim() || !me) return;
    await supabase.from("profiles").update({ name: v.trim().slice(0, 100) }).eq("id", me.id);
    inv("me", "operators"); toast.success("Nome atualizado");
  };
  const toggleRole = async (uid: string, role: "admin" | "manager", has: boolean) => {
    const { error } = has
      ? await supabase.from("user_roles").delete().eq("user_id", uid).eq("role", role)
      : await supabase.from("user_roles").insert({ user_id: uid, role });
    if (error) { toast.error(error.message); return; }
    inv("operators", "me");
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Operadores</h1>
      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base">Meu nome de operador</CardTitle></CardHeader>
        <CardContent>{me && <Input key={me.profile.name} defaultValue={me.profile.name} onBlur={(e) => rename(e.target.value)} className="max-w-sm" />}</CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base">Equipe e permissões ({ops.length})</CardTitle></CardHeader>
        <CardContent className="divide-y">
          <div className="space-y-0.5 pb-2 text-xs text-muted-foreground">
            <p><b>Operador:</b> preenche Plantão e Relatório semanal.</p>
            <p><b>Cadastros:</b> também cria e altera clientes, usuários, dispositivos, sensores e campos.</p>
            <p><b>Admin:</b> acesso total, incluindo arquivamento, exclusões e permissões.</p>
            <p>Novos cadastros aguardam autorização do administrador.</p>
          </div>
          {ops.map((o) => (
            <div key={o.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
              <div><span className="font-medium">{o.name}</span> <span className="text-muted-foreground">{o.email}</span></div>
              <div className="flex items-center gap-2">
                <span className="rounded bg-accent px-1.5 py-0.5 text-xs">{o.status === 'pending' ? 'aguardando aprovação' : o.status === 'removed' ? 'removido' : o.isAdmin ? "admin" : o.isManager ? "cadastros" : "operador"}</span>
                {me && canChangeOperator(me.isAdmin, me.id, o.id) && o.status !== 'removed' && (
                  <>
                    {o.status === 'pending' ? <Button size="sm" onClick={() => approve(o.id)}>Autorizar cadastro</Button> : <>
                      {!o.isAdmin && <Button size="sm" variant="outline" onClick={() => toggleRole(o.id, "manager", o.isManager)}>{o.isManager ? "Revogar cadastros" : "Conceder cadastros"}</Button>}
                      <Button size="sm" variant="outline" onClick={() => toggleRole(o.id, "admin", o.isAdmin)}>{o.isAdmin ? "Remover admin" : "Tornar admin"}</Button>
                    </>}
                    <Button size="sm" variant="destructive" onClick={() => deleteOperator(o.id, o.name)}>Excluir cadastro</Button>
                  </>
                )}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base">Sessões de plantão</CardTitle></CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-muted-foreground"><tr><th className="py-1">Operador</th><th>Turno</th><th>Início</th><th>Fim</th><th>Passou para</th></tr></thead>
            <tbody>{shifts.map((s) => (
              <tr key={s.id} className="border-t"><td className="py-1.5 font-medium">{name(s.operator_id)}</td><td>{s.shift_type}</td><td className="font-mono">{fmt(s.started_at)}</td><td className="font-mono">{fmt(s.ended_at)}</td><td>{name(s.next_operator_id)}</td></tr>
            ))}</tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
