# Cloudflare Tunnel + Access for the +EV companion API

Goal: expose the FastAPI service (localhost:8000 on EC2) to the iOS app with ZERO
inbound ports open, authenticated by a Cloudflare Access service token.

## 1. Domain
Buy a domain at Cloudflare Registrar (~$10/yr) or move an existing one to
Cloudflare Free DNS.

## 2. Install cloudflared on EC2
```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@3.146.180.145
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
  sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | \
  sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
```

## 3. Create + route the tunnel
```bash
cloudflared tunnel login                       # browser auth, pick your domain
cloudflared tunnel create softbook-api         # writes ~/.cloudflared/<UUID>.json
cloudflared tunnel route dns softbook-api api.<your-domain>
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/<UUID>.json /etc/cloudflared/
sudo cp /home/ubuntu/kalshi-arb/deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo sed -i 's/<TUNNEL_UUID>/<UUID>/g; s/<your-domain>/example.com/g' /etc/cloudflared/config.yml
sudo cloudflared --config /etc/cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
```

## 4. Cloudflare Access (service token)
In Zero Trust dashboard:
1. Access > Service Auth > Service Tokens > Create. Save Client Id + Secret
   (secret shown once).
2. Access > Applications > Add a self-hosted app for `api.<your-domain>`.
3. Add ONE policy: Action = **Service Auth**, Include = the service token.
   (Optional 2nd policy: Action=Allow, Include=One-Time PIN + your email, for
   browser access.)
4. Copy the application **AUD** tag and your team domain
   (`<team>.cloudflareaccess.com`).

## 5. EC2 .env additions
```
APP_API_TOKEN=<long random string>
CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
CF_ACCESS_AUD=<application AUD tag>
```

## 6. Close inbound ports
The tunnel is outbound-only. Remove inbound API rules from the EC2 security group
and ufw (keep your SSH rule). Break-glass: re-add a temporary SG rule, or install
Tailscale, if Cloudflare has an incident.

## 7. Install + start the app services
```bash
cd /home/ubuntu/kalshi-arb && git pull
sudo cp deploy/kalshi-softbook-api.service /etc/systemd/system/
sudo cp deploy/kalshi-softbook-push.service /etc/systemd/system/
sudo cp deploy/kalshi-softbook-push.timer /etc/systemd/system/
sudo cp deploy/kalshi-softbook-live-grade.service /etc/systemd/system/
sudo cp deploy/kalshi-softbook-live-grade.timer /etc/systemd/system/
sudo cp deploy/kalshi-softbook-app-settle.service /etc/systemd/system/
sudo cp deploy/kalshi-softbook-app-settle.timer /etc/systemd/system/
venv/bin/python -m pip install -r requirements.txt        # picks up PyJWT
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-softbook-api.service
sudo systemctl enable --now kalshi-softbook-push.timer
sudo systemctl enable --now kalshi-softbook-live-grade.timer
sudo systemctl enable --now kalshi-softbook-app-settle.timer   # settles app_bet -> calendar PnL + settle push
```

## 8. Smoke test (from your laptop)
```bash
curl -s https://api.<your-domain>/picks \
  -H "Authorization: Bearer <APP_API_TOKEN>" \
  -H "CF-Access-Client-Id: <client-id>" \
  -H "CF-Access-Client-Secret: <client-secret>" | head
```
Expect a JSON `{"picks":[...],"counts":{...}}`. A 401 = bad bearer; a Cloudflare
login page = the Access policy isn't Service Auth.
