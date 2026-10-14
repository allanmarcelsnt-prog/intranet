# Rotas do gateway (incluído pelos blocos server de nginx.http.conf / nginx.https.conf):
#   /auth/v1/  -> GoTrue (login)      /rest/v1/ -> PostgREST (dados)      demais -> app
proxy_http_version 1.1;
proxy_set_header Host              $http_host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $http_host;
proxy_read_timeout 60s;

location /auth/v1/ {
    set $up_auth http://auth:9999;
    rewrite ^/auth/v1/(.*)$ /$1 break;
    proxy_pass $up_auth;
}

location /rest/v1/ {
    set $up_rest http://rest:3000;
    rewrite ^/rest/v1/(.*)$ /$1 break;
    proxy_pass $up_rest;
}

location / {
    set $up_app http://app:3000;
    proxy_pass $up_app;
}
