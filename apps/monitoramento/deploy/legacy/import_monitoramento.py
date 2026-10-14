#!/usr/bin/env python3
"""Importa os dados do app antigo (monitoramento.json + monitoramento_cadastros.json)
para o modelo do shift-guardian. Gera um arquivo SQL (uma transação) e imprime um relatório.
NÃO grava no banco: a aplicação do SQL é um passo separado (veja deploy/README.md).

Uso:  python3 import_monitoramento.py DADOS.json CADASTROS.json [-o import-legado.sql]
"""
import argparse, json, re, sys, unicodedata, uuid, collections

DEVICE_TYPE = {"Controle Remoto": "Controle remoto", "Aplicativo My Security": "Aplicativo",
               "Teclado": "Teclado", "Monitoramento Reggra": "Central de monitoramento"}
SENSOR_TYPE = {"IVP": "Sensor infravermelho", "Sensor Magnético": "Sensor magnético",
               "Sensor de Barreira": "Barreira"}  # demais tipos entram com o texto original
OPERATOR_DEVICE = "Monitoramento Reggra"  # armes/desarmes feitos pelo monitoramento -> by_operator


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def q(v):
    if v is None or v == "":
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def arr(ids):
    return "ARRAY[" + ",".join(f"'{i}'::uuid" for i in ids) + "]::uuid[]" if ids else "'{}'::uuid[]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dados"); ap.add_argument("cadastros")
    ap.add_argument("-o", "--out", default="import-legado.sql")
    a = ap.parse_args()
    m = json.load(open(a.dados, encoding="utf-8"))
    c = json.load(open(a.cadastros, encoding="utf-8"))

    out, rep = [], collections.Counter()
    warn = []
    out.append("-- Importação do app antigo. Gerado por import_monitoramento.py")
    out.append("BEGIN;")
    out.append("DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.clients WHERE notes LIKE '%[legado:%') "
               "THEN RAISE EXCEPTION 'Importação já realizada (clientes legado encontrados).'; END IF; END $$;")

    cid, sensors = {}, {}  # código -> uuid ; código -> {nome normalizado: (uuid,label)}
    for cl in c["clientes"]:
        code = cl["codigo"]; cid[code] = str(uuid.uuid4())
        end = cl.get("endereco") or ""
        parts = [end]
        cidade = " - ".join(x for x in [cl.get("cidade"), cl.get("estado")] if x)
        if cl.get("cidade") and norm(cl["cidade"]) not in norm(end): parts.append(cidade)  # evita repetir cidade já no endereço
        cep = cl.get("cep") or ""
        if cep and re.sub(r"\D", "", cep) not in re.sub(r"\D", "", end): parts.append(cep)
        addr = ", ".join(x for x in parts if x)
        notes = f"[legado:{code}] Código: {code}" + (f" | Responsável: {cl['responsavel']}" if cl.get("responsavel") else "")
        out.append(f"INSERT INTO public.clients(id,name,address,notes) VALUES ('{cid[code]}',{q(cl['nome'])},{q(addr)},{q(notes)});")
        rep["clientes"] += 1
    for code, us in c["usuarios"].items():
        for u in us:
            out.append(f"INSERT INTO public.client_users(client_id,name,role) VALUES ('{cid[code]}',{q(u['nome'])},{q(u.get('cargo'))});"); rep["usuarios"] += 1
    for code in cid:  # a lista de dispositivos do app antigo é global -> cada cliente recebe todos
        for d in c["dispositivos"]:
            out.append(f"INSERT INTO public.client_devices(client_id,name,type) VALUES ('{cid[code]}',{q(d)},{q(DEVICE_TYPE.get(d, 'Teclado'))});"); rep["dispositivos"] += 1
    for code, ss in c["sensores"].items():
        sensors[code] = {}
        for s in ss:
            sid = str(uuid.uuid4())
            name = s["nome"] + (f" — {s['localizacao']}" if s.get("localizacao") else "")
            if s.get("obs"): name += f" ({s['obs']})"
            out.append(f"INSERT INTO public.client_sensors(id,client_id,zone,name,type,active) VALUES ('{sid}','{cid[code]}',{q(s.get('zona') or '-')},{q(name)},"
                       f"{q(SENSOR_TYPE.get(s['tipo'], s['tipo']))},{'true' if s.get('status','Ativo')=='Ativo' else 'false'});")
            sensors[code][norm(s["nome"])] = sid; rep["sensores"] += 1

    def ev(code, kind, date, time=None, end=None, user=None, device=None, byop=False, sids=(), labels=None, desc=None, status=None, custom=None):
        out.append("INSERT INTO public.client_events(client_id,kind,event_date,event_time,end_time,user_name,device_name,by_operator,sensor_ids,sensor_labels,description,status,custom) VALUES "
                   f"('{cid[code]}','{kind}',{q(date)},{q(time)},{q(end)},{q(user)},{q(device)},{'true' if byop else 'false'},{arr(sids)},{q(labels)},{q(desc)},{q(status)},{q(json.dumps(custom or {}, ensure_ascii=False))}::jsonb);")
        rep["eventos:" + kind] += 1

    for code, weeks in m["semanas"].items():
        if code not in cid: warn.append(f"semanas de cliente sem cadastro: {code}"); continue
        for monday, w in sorted(weeks.items()):
            for k, kind in (("armes", "arm"), ("desarmes", "disarm")):
                for x in w.get(k, []):
                    ev(code, kind, x["data"], x.get("hora"), user=x.get("usuario") or None, device=x.get("dispositivo") or None, byop=x.get("dispositivo") == OPERATOR_DEVICE)
            for x in w.get("disparos", []):
                labels = x.get("sensores") or []; sids = []
                for s in labels:
                    sid = sensors.get(code, {}).get(norm(s))
                    if sid: sids.append(sid); rep["disparo-sensor:vinculado"] += 1
                    else: rep["disparo-sensor:só texto"] += 1
                cont = x.get("continuo")
                desc = " — ".join(p for p in [x.get("motivo"), x.get("observacoes") or x.get("observacao")] if p)
                ev(code, "trigger", x["data"], x.get("inicioContinuo") if cont else x.get("hora"), end=x.get("fimContinuo") if cont else None,
                   sids=sids, labels=", ".join(labels) or None, desc=desc or None, custom={"disparo_continuo": True} if cont else {"importado": True})
            for x in w.get("zonas", []):
                if "status" not in x:  # zona anulada/aberta no arme -> bypass encerrado no mesmo dia
                    out.append("INSERT INTO public.bypasses(client_id,sensor_label,reason,start_date,end_date) VALUES "
                               f"('{cid[code]}',{q(x['zona'])},{q(' — '.join(p for p in [x.get('motivo'), ('Usuário: ' + x['usuario']) if x.get('usuario') else ''] if p))},{q(x['data'])},{q(x['data'])});")
                    rep["bypasses"] += 1
                else:  # registro de acompanhamento (zona fechada etc.)
                    ev(code, "observation", x["data"], desc=f"Zona {x['zona']}" + (f" ({x['sensor']})" if x.get("sensor") else "") + f" — {x.get('status','')}")
            for x in w.get("visitas", []):
                ev(code, "maintenance", x["data"], desc="Visita técnica — " + "; ".join(f"{l}: {x[k]}" for k, l in (("equipe", "equipe"), ("veiculo", "veículo"), ("recebido_por", "recebido por"), ("relato", "relato")) if x.get(k)))
            if (w.get("observacoes") or "").strip():
                ev(code, "observation", monday, desc="[Obs. semanal] " + w["observacoes"].strip())
    for code, ac in m.get("acervo", {}).items():
        for tag, key in (("Ponto de atenção", "pontos"), ("Anormalidade técnica", "anormalidades")):
            for x in ac.get(key, []):
                d = x.get("descricao", "")
                extra = "; ".join(f"{l}: {x[k]}" for k, l in (("prioridade", "prioridade"), ("responsavel", "responsável"), ("data_resolucao", "resolvido em"), ("solucao", "solução")) if x.get(k))
                ev(code, "observation", x.get("data_identificacao") or None, desc=f"[{tag}] {d}" + (f" ({extra})" if extra else ""), status=x.get("status"))
    out.append("COMMIT;")
    open(a.out, "w", encoding="utf-8").write("\n".join(out) + "\n")

    print("== Prévia da importação (nada foi gravado) ==")
    for k, v in sorted(rep.items()): print(f"  {k}: {v}")
    for w_ in warn: print("  AVISO:", w_)
    print(f"SQL gerado: {a.out} ({len(out)} linhas)")


if __name__ == "__main__":
    sys.exit(main())
