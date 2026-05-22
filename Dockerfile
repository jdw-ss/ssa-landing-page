FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
COPY robots.txt /usr/share/nginx/html/robots.txt
COPY help/ /usr/share/nginx/html/help/
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
