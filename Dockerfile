FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    FFMPEG_BIN=/usr/bin/ffmpeg

RUN apt-get update && apt-get install -y --no-install-recommends \
        apache2 libapache2-mod-wsgi-py3 python3 python3-venv curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libvpx-vp9

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY web_service.py apply_zoom.py app.wsgi ./
COPY docker/apache/videos.conf /etc/apache2/sites-available/videos.conf
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh \
 && echo "ServerName localhost" > /etc/apache2/conf-available/servername.conf \
 && a2enconf servername \
 && a2dissite 000-default \
 && a2ensite videos \
 && mkdir -p /app/uploads \
 && chown -R www-data:www-data /app/uploads

EXPOSE 80
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
