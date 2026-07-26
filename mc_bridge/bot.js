/**
 * HugoSMP — Minecraft↔Discord Bridge
 *
 * Erkennt:
 *   1. Verifizierungscodes  (/msg BotName DC-XXXXXXXX)
 *   2. /pay-Transaktionen   (diverse Economy-Plugin-Formate)
 *
 * Beide Events werden via HTTP POST an den Discord-Bot gemeldet.
 *
 * .env Variablen (mc_bridge/.env):
 *   MC_HOST         — Minecraft-Server IP/Domain  (z.B. hugosmp.net)
 *   MC_PORT         — Minecraft-Server Port       (Standard: 25565)
 *   MC_USERNAME     — Microsoft-E-Mail des Bot-Accounts
 *   MC_AUTH         — "microsoft" oder "offline"
 *   BRIDGE_PORT     — Port der lokalen HTTP-API   (Standard: 8765)
 *   DISCORD_BOT_URL — URL des Discord-Bots        (Standard: http://127.0.0.1:8766)
 *   BRIDGE_SECRET   — Gemeinsames Secret
 */

require("dotenv").config();
const mineflayer = require("mineflayer");
const express    = require("express");

// ── Konfiguration ──────────────────────────────────────────────────────────────
const MC_HOST         = process.env.MC_HOST         || "hugosmp.net";
const MC_PORT         = parseInt(process.env.MC_PORT || "25565");
const MC_USERNAME     = process.env.MC_USERNAME      || "";
const MC_PASSWORD     = process.env.MC_PASSWORD      || "";
const MC_AUTH         = process.env.MC_AUTH          || "microsoft";
const BRIDGE_PORT     = parseInt(process.env.BRIDGE_PORT || "8765");
const DISCORD_BOT_URL = process.env.DISCORD_BOT_URL  || "http://127.0.0.1:8766";
const BRIDGE_SECRET   = process.env.BRIDGE_SECRET    || "changeme";

let bot = null;

// ── Pay-Patterns ───────────────────────────────────────────────────────────────
// Jedes Pattern muss die Gruppen liefern: sender, amount, currency?, receiver?
// Wir versuchen möglichst viele Economy-Plugin-Formate abzudecken.
const PAY_PATTERNS = [
    // EssentialsX EN:  "Steve paid Alex $500.00."
    {
        re: /^(\S+)\s+paid\s+(\S+)\s+\$?([\d.,]+)/i,
        map: (m) => ({ sender: m[1], receiver: m[2], amount: m[3], currency: "$" }),
    },
    // EssentialsX DE:  "Steve hat Alex 500.00 $ bezahlt."
    {
        re: /^(\S+)\s+hat\s+(\S+)\s+([\d.,]+)\s*(\S*)\s+bezahlt/i,
        map: (m) => ({ sender: m[1], receiver: m[2], amount: m[3], currency: m[4] || "Coins" }),
    },
    // CMI:  "[Economy] Steve → Alex: 500 Coins"
    {
        re: /\[Economy\]\s+(\S+)\s*[→->]+\s*(\S+):\s*([\d.,]+)\s*(\S*)/i,
        map: (m) => ({ sender: m[1], receiver: m[2], amount: m[3], currency: m[4] || "Coins" }),
    },
    // Generic "paid":  "Steve paid 500 coins to Alex"
    {
        re: /^(\S+)\s+paid\s+([\d.,]+)\s*(\S*)\s+to\s+(\S+)/i,
        map: (m) => ({ sender: m[1], receiver: m[4], amount: m[2], currency: m[3] || "Coins" }),
    },
    // Generic "transferred":  "500 coins transferred from Steve to Alex"
    {
        re: /^([\d.,]+)\s*(\S*)\s+transferred\s+from\s+(\S+)\s+to\s+(\S+)/i,
        map: (m) => ({ sender: m[3], receiver: m[4], amount: m[1], currency: m[2] || "Coins" }),
    },
    // "überwiesen" / "überwies":  "Steve überwies Alex 500 Coins"
    {
        re: /^(\S+)\s+überwies(?:en)?\s+(\S+)\s+([\d.,]+)\s*(\S*)/i,
        map: (m) => ({ sender: m[1], receiver: m[2], amount: m[3], currency: m[4] || "Coins" }),
    },
    // "zahlt" / "zahlte":  "Steve zahlte 500 Coins an Alex"
    {
        re: /^(\S+)\s+zahlte?\s+([\d.,]+)\s*(\S*)\s+an\s+(\S+)/i,
        map: (m) => ({ sender: m[1], receiver: m[4], amount: m[2], currency: m[3] || "Coins" }),
    },
    // Vault/generic bracket:  "[Pay] Steve -> Alex: 500"
    {
        re: /\[Pay(?:ment)?\]\s+(\S+)\s*[-→>]+\s*(\S+):\s*([\d.,]+)\s*(\S*)/i,
        map: (m) => ({ sender: m[1], receiver: m[2], amount: m[3], currency: m[4] || "Coins" }),
    },
    // "erhalten" / "received":  "Alex hat 500 Coins von Steve erhalten"
    {
        re: /^(\S+)\s+hat\s+([\d.,]+)\s*(\S*)\s+von\s+(\S+)\s+erhalten/i,
        map: (m) => ({ sender: m[4], receiver: m[1], amount: m[2], currency: m[3] || "Coins" }),
    },
    // "received":  "Alex received 500 coins from Steve"
    {
        re: /^(\S+)\s+received\s+([\d.,]+)\s*(\S*)\s+from\s+(\S+)/i,
        map: (m) => ({ sender: m[4], receiver: m[1], amount: m[2], currency: m[3] || "Coins" }),
    },
];

// ── Express HTTP-API ───────────────────────────────────────────────────────────
const app = express();
app.use(express.json());

app.get("/health", (req, res) => {
    res.json({
        online:   bot !== null && bot.entity !== null,
        username: bot ? bot.username : null,
        server:   `${MC_HOST}:${MC_PORT}`,
    });
});

app.post("/send-message", (req, res) => {
    if (req.body.secret !== BRIDGE_SECRET)
        return res.status(403).json({ error: "Unauthorized" });
    if (!bot)
        return res.status(503).json({ error: "MC-Bot nicht verbunden" });
    try {
        bot.chat(req.body.message);
        res.json({ ok: true });
    } catch (e) {
        res.status(500).json({ error: String(e) });
    }
});

app.listen(BRIDGE_PORT, "127.0.0.1", () => {
    console.log(`[Bridge] HTTP-API läuft auf Port ${BRIDGE_PORT}`);
});

// ── Mineflayer Bot ─────────────────────────────────────────────────────────────

function createBot() {
    console.log(`[MC] Verbinde mit ${MC_HOST}:${MC_PORT} als ${MC_USERNAME} (${MC_AUTH})...`);

    const options = {
        host:       MC_HOST,
        port:       MC_PORT,
        username:   MC_USERNAME,
        auth:       MC_AUTH,
        version:    false,
        hideErrors: false,
    };
    if (MC_AUTH !== "microsoft" && MC_PASSWORD)
        options.password = MC_PASSWORD;

    bot = mineflayer.createBot(options);

    bot.once("spawn", () =>
        console.log(`[MC] ✅ Eingeloggt als ${bot.username} auf ${MC_HOST}`)
    );

    // Primär: messagestr liefert den vollständigen formatierten String
    bot.on("messagestr", (raw) => {
        processRaw(raw);
    });

    // Fallback für Server die chat/whisper separat feuern
    bot.on("chat", (username, message) => {
        if (username === bot.username) return;
        checkCode(username, message);
        checkPay(message, username);
    });

    bot.on("whisper", (username, message) => {
        checkCode(username, message);
    });

    bot.on("kicked", (reason) => {
        console.warn(`[MC] Gekickt: ${reason} — Reconnect in 15s`);
        bot = null;
        setTimeout(createBot, 15_000);
    });
    bot.on("error", (e)  => console.error(`[MC] Fehler: ${e.message}`));
    bot.on("end",   ()   => {
        console.warn("[MC] Verbindung getrennt — Reconnect in 15s");
        bot = null;
        setTimeout(createBot, 15_000);
    });
}

// ── Nachrichten-Verarbeitung ───────────────────────────────────────────────────

/**
 * Haupteingang für alle rohen Chat-Strings.
 * Prüft zuerst auf Whisper-/Verify-Format, dann auf Pay-Patterns.
 */
function processRaw(raw) {
    // ── Whisper / Verify-Code ──────────────────────────────────────────────
    // Format "[Sender -> Empfänger]: Inhalt"
    const whisper1 = raw.match(/^\[(.+?)\s*->\s*.+?\]:\s*(.+)$/);
    if (whisper1) {
        checkCode(whisper1[1].trim(), whisper1[2].trim());
        return; // Whisper sind nie Pay-Nachrichten
    }
    // Format "Sender flüstert dir: Inhalt"
    const whisper2 = raw.match(/^(\S+)\s+(?:flüstert|whispers?|tells? you)\s*(?:dir|you)?:?\s*(.+)$/i);
    if (whisper2) {
        checkCode(whisper2[1].trim(), whisper2[2].trim());
        return;
    }
    // Fallback Verify-Code ohne erkennbaren Sender
    const codeOnly = raw.match(/\b(DC-[A-Z0-9]{8})\b/);
    if (codeOnly) {
        postToDiscord("/mc-verify", { secret: BRIDGE_SECRET, mc_username: "UNKNOWN", code: codeOnly[1] });
        return;
    }

    // ── Pay ────────────────────────────────────────────────────────────────
    checkPay(raw, null);
}

/** Prüft ob eine Nachricht einen DC-Verify-Code enthält. */
function checkCode(mcUsername, message) {
    const m = message.match(/\b(DC-[A-Z0-9]{8})\b/);
    if (!m) return;
    console.log(`[Verify] Code ${m[1]} von ${mcUsername}`);
    postToDiscord("/mc-verify", {
        secret:      BRIDGE_SECRET,
        mc_username: mcUsername,
        code:        m[1],
    });
}

/**
 * Prüft ob eine Chat-Nachricht eine /pay-Transaktion enthält.
 * @param {string} raw     - Die rohe Chat-Nachricht
 * @param {string|null} chatSender - Sender falls aus chat-Event bekannt
 */
function checkPay(raw, chatSender) {
    // Färbungszeichen (§x) entfernen
    const clean = raw.replace(/§[0-9a-fk-or]/gi, "").trim();

    for (const { re, map } of PAY_PATTERNS) {
        const m = clean.match(re);
        if (!m) continue;

        const tx = map(m);

        // Betrag normalisieren: Komma→Punkt, dann als Float
        const amount = parseFloat(tx.amount.replace(",", "."));
        if (isNaN(amount) || amount <= 0) continue;

        // Währungs-Garbage-Strings filtern
        const currency = /^[a-zA-Z$€£¥₿Cc]/.test(tx.currency) ? tx.currency : "Coins";

        console.log(`[Pay] ${tx.sender} → ${tx.receiver}: ${amount} ${currency}  |  Raw: "${clean}"`);

        postToDiscord("/mc-pay", {
            secret:   BRIDGE_SECRET,
            sender:   tx.sender   || chatSender || "?",
            receiver: tx.receiver || "?",
            amount:   amount,
            currency: currency,
            raw:      clean,
        });
        return; // erstes Match reicht
    }
}

// ── HTTP-Hilfsfunktion ─────────────────────────────────────────────────────────

function postToDiscord(path, payload) {
    const body  = JSON.stringify(payload);
    const url   = new URL(path, DISCORD_BOT_URL);
    const isHttp = url.protocol === "http:";
    const lib   = isHttp ? require("http") : require("https");

    const req = lib.request(
        {
            hostname: url.hostname,
            port:     url.port || (isHttp ? 80 : 443),
            path:     url.pathname,
            method:   "POST",
            headers: {
                "Content-Type":   "application/json",
                "Content-Length": Buffer.byteLength(body),
            },
        },
        (res) => {
            let buf = "";
            res.on("data", (c) => (buf += c));
            res.on("end",  ()  => console.log(`[Bridge→Discord] ${path} → ${res.statusCode} ${buf}`));
        }
    );
    req.on("error", (e) => console.error(`[Bridge] POST-Fehler: ${e.message}`));
    req.write(body);
    req.end();
}

// ── Start ──────────────────────────────────────────────────────────────────────
createBot();
