#!/bin/bash
# ============================================================
# Monitor DNS interno Safenet  (versao com auto-recuperacao)
# Verifica a resolucao de intranet.safenet.lan no servidor
# DNS interno (192.168.0.171 / container safenet-dns) e envia
# alerta por e-mail (via midclt) quando a resolucao falha.
#
# Novidade (02/10/2026): apos 2 falhas seguidas (10 min), grava
# um arquivo de evidencia do estado do dnsmasq e reinicia o
# container safenet-dns. No maximo 1 reinicio a cada 30 min.
# Causa conhecida: dnsmasq trava lendo uma conexao TCP de DNS
# que um cliente abriu e nao completou (fila UDP enche).
#
# Rodar no HOST TrueNAS (nao dentro do container).
# Agendar via cron da WebUI: a cada 5 minutos.
#
# Modo de teste (nao envia e-mail, nao reinicia, usa /tmp):
#   TESTE=1 HOSTNAME_CHECK=naoexiste.safenet.lan /caminho/monitor_dns.sh
#
# Autor: TI Safenet  |  Criado apos incidente DNS de 29/07/2026
# ============================================================

# ---------------------- Configuracao ------------------------
DNS_SERVER="192.168.0.171"                       # dnsmasq do container safenet-dns
HOSTNAME_CHECK="${HOSTNAME_CHECK:-intranet.safenet.lan}"   # nome interno a validar
IP_ESPERADO="192.168.0.171"                      # resposta correta esperada
CONTAINER="safenet-dns"                          # container a reiniciar
# Destinatarios do alerta (separados por espaco)
ALERT_TO="allan.marcel@safenettecnologia.com.br carlos.almeida@safenettecnologia.com.br rodrigoiannibelli@safenettecnologia.com.br"
STATE_FILE="/root/.dns_monitor_state"            # controle anti-spam
FALHAS_FILE="/root/.dns_monitor_falhas"          # contador de falhas seguidas
RESTART_FILE="/root/.dns_monitor_ultimo_restart" # horario do ultimo reinicio
LOG_FILE="/var/log/monitor_dns.log"              # historico de eventos
EVID_DIR="/var/log/monitor_dns_evidencia"        # evidencias coletadas antes do reinicio
COOLDOWN=1800                                    # 30 min entre alertas/reinicios (s)
FALHAS_PARA_REINICIAR=2                          # falhas seguidas ate reiniciar (2 x 5 min)
TESTE="${TESTE:-0}"
# ------------------------------------------------------------

# Modo de teste: isola tudo em /tmp e desliga acoes reais
if [ "$TESTE" = "1" ]; then
    STATE_FILE="/tmp/dns_monitor_state_teste"
    FALHAS_FILE="/tmp/dns_monitor_falhas_teste"
    RESTART_FILE="/tmp/dns_monitor_ultimo_restart_teste"
    LOG_FILE="/tmp/monitor_dns_teste.log"
    EVID_DIR="/tmp/monitor_dns_evidencia_teste"
fi

AGORA=$(date +%s)
DATA_HUMANA=$(date '+%d/%m/%Y %H:%M:%S')

# Grava um arquivo com o estado do dnsmasq no momento da falha.
coletar_evidencia() {
    mkdir -p "$EVID_DIR"
    local arq="$EVID_DIR/$(date '+%Y%m%d-%H%M%S').txt"
    local pid
    pid=$(pgrep -x dnsmasq | head -n1)
    {
        echo "== Evidencia DNS $DATA_HUMANA =="
        echo "-- container"
        timeout 10 docker inspect -f 'status={{.State.Status}} restarts={{.RestartCount}} oom={{.State.OOMKilled}} iniciado={{.State.StartedAt}}' "$CONTAINER"
        echo "-- pid do dnsmasq: ${pid:-nao encontrado}"
        if [ -n "$pid" ]; then
            ps -o pid,stat,wchan:32,etime,time,cmd -p "$pid"
            echo "-- syscall"
            cat "/proc/$pid/syscall"
            echo "-- descritores abertos"
            ls -l "/proc/$pid/fd"
            echo "-- conexoes tcp do dnsmasq"
            ss -tnp | grep "pid=$pid,"
        fi
        echo "-- sockets escutando na porta 53"
        ss -lntup 'sport = :53'
        echo "-- conexoes tcp estabelecidas na porta 53 (clientes)"
        ss -tn state established '( sport = :53 )'
        echo "-- ultimas linhas do log do container"
        timeout 15 docker logs --tail 30 "$CONTAINER" 2>&1
    } > "$arq" 2>&1
    echo "$DATA_HUMANA  EVIDENCIA  (gravada em $arq)" >> "$LOG_FILE"
}

# Consulta DNS a partir do host, mirando o dnsmasq do container.
# +time/+tries evitam travamento caso o servidor nao responda.
RESULT=$(dig +short +time=3 +tries=2 @"$DNS_SERVER" "$HOSTNAME_CHECK" A 2>/dev/null | head -n1)

# ---------------------- Caso OK -----------------------------
if [ "$RESULT" = "$IP_ESPERADO" ]; then
    # Se havia falha registrada, loga a recuperacao e limpa o estado.
    if [ -f "$STATE_FILE" ]; then
        echo "$DATA_HUMANA  RECUPERADO  ($HOSTNAME_CHECK -> $RESULT)" >> "$LOG_FILE"
        rm -f "$STATE_FILE"
    fi
    rm -f "$FALHAS_FILE"
    exit 0
fi

# -------------------- Caso FALHA ----------------------------
echo "$DATA_HUMANA  FALHA  (esperado $IP_ESPERADO, recebido '${RESULT:-vazio}')" >> "$LOG_FILE"

# Conta falhas seguidas
FALHAS=0
if [ -f "$FALHAS_FILE" ]; then
    FALHAS=$(cat "$FALHAS_FILE" 2>/dev/null || echo 0)
fi
FALHAS=$((FALHAS + 1))
echo "$FALHAS" > "$FALHAS_FILE"

# Auto-recuperacao: apos N falhas seguidas, coleta evidencia e reinicia
# o container (no maximo 1 vez a cada COOLDOWN segundos).
if [ "$FALHAS" -ge "$FALHAS_PARA_REINICIAR" ]; then
    ULT_RESTART=0
    if [ -f "$RESTART_FILE" ]; then
        ULT_RESTART=$(cat "$RESTART_FILE" 2>/dev/null || echo 0)
    fi
    DELTA_R=$((AGORA - ULT_RESTART))
    if [ "$ULT_RESTART" -eq 0 ] || [ "$DELTA_R" -ge "$COOLDOWN" ]; then
        coletar_evidencia
        echo "$AGORA" > "$RESTART_FILE"
        if [ "$TESTE" = "1" ]; then
            echo "$DATA_HUMANA  REINICIO  ([TESTE] docker restart $CONTAINER nao executado)" >> "$LOG_FILE"
        else
            echo "$DATA_HUMANA  REINICIO  (docker restart $CONTAINER apos $FALHAS falhas seguidas)" >> "$LOG_FILE"
            timeout 60 docker restart "$CONTAINER" >/dev/null 2>&1
        fi
    fi
fi

# Trava anti-spam: so envia se ja passou o cooldown desde o ultimo alerta.
ULTIMO=0
if [ -f "$STATE_FILE" ]; then
    ULTIMO=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
fi
DELTA=$((AGORA - ULTIMO))
if [ "$ULTIMO" -ne 0 ] && [ "$DELTA" -lt "$COOLDOWN" ]; then
    exit 1   # ja alertou recentemente; nao repete o e-mail
fi

# Registra o momento deste alerta (reinicia o cooldown)
echo "$AGORA" > "$STATE_FILE"

# Em modo de teste nao envia e-mail
if [ "$TESTE" = "1" ]; then
    echo "$DATA_HUMANA  ALERTA  ([TESTE] e-mail nao enviado)" >> "$LOG_FILE"
    exit 1
fi

# Monta o corpo do e-mail
CORPO="ALERTA: A resolucao DNS interna falhou.

Nome consultado: $HOSTNAME_CHECK
Servidor DNS:    $DNS_SERVER
Esperado:        $IP_ESPERADO
Recebido:        ${RESULT:-(vazio / sem resposta)}
Horario:         $DATA_HUMANA

A intranet pode estar inacessivel pelo nome de dominio
(http://intranet.safenet.lan:8081). O acesso por IP
(http://192.168.0.171:8081) provavelmente ainda funciona.

Auto-recuperacao: se a falha persistir por 10 minutos (2 verificacoes
seguidas), o monitor grava a evidencia em $EVID_DIR e reinicia o
container $CONTAINER automaticamente (no maximo 1 vez a cada 30 min).

Acao manual, se a falha continuar apos o reinicio automatico:
  1) docker restart $CONTAINER
  2) Validar do host: nslookup $HOSTNAME_CHECK $DNS_SERVER
  3) No cliente afetado: ipconfig /flushdns

Este alerta se repete no maximo a cada 30 minutos enquanto a
falha persistir. Um e-mail de recuperacao nao e enviado; a volta
ao normal fica registrada em $LOG_FILE.

-- Monitor automatico de DNS / TrueNAS Safenet"

# Monta o payload JSON inteiro em Python (robusto a acentos, quebras de
# linha e multiplos destinatarios). O corpo vem por stdin; os e-mails,
# como argumentos.
PAYLOAD=$(printf '%s' "$CORPO" | python3 -c '
import json, sys
corpo = sys.stdin.read()
destinatarios = sys.argv[1:]
print(json.dumps({
    "subject": "[ALERTA] DNS interno Safenet falhou",
    "text": corpo,
    "to": destinatarios,
}))
' $ALERT_TO)

midclt call mail.send "$PAYLOAD" >/dev/null 2>&1

exit 1
