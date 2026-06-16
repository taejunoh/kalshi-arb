// app/src/deeplink.ts
import * as Linking from "expo-linking";
import * as Clipboard from "expo-clipboard";
import type { Deeplink } from "./api/types";
import { VERIFIED_PREFILL_BOOKS } from "./config";

export function isVerifiedPrefill(book: string, dl: Deeplink): boolean {
  return dl.tier === "prefill" && VERIFIED_PREFILL_BOOKS.includes(book);
}

export type OpenResult = "prefilled" | "opened_and_copied" | "copied";

/** Open the server-built link via the system handler, copying the bet text unless the slip
 *  is being pre-filled. Three outcomes the caller turns into user feedback:
 *   - "prefilled":        a VERIFIED prefill (isPrefill) opened straight to a populated slip
 *                          — we do NOT copy (the bet text would be noise).
 *   - "opened_and_copied": the app/event page opened but the slip is NOT pre-filled (Fanatics
 *                          home, BetMGM/theScore event, …) — we ALSO copy the bet text so the
 *                          user can paste it into the book's search / bet entry.
 *   - "copied":           no app opened (no link, or a custom scheme whose app isn't installed)
 *                          — we copy the text. Never a silent no-op.
 *  https universal links are always openable; custom schemes (dksb://) must pass canOpenURL,
 *  which can throw on iOS when the scheme isn't whitelisted — treat a throw as "not installed". */
export async function openOrCopy(dl: Deeplink, isPrefill = false): Promise<OpenResult> {
  const { url, copy_text } = dl;
  let opened = false;
  if (url) {
    const isHttp = url.startsWith("http://") || url.startsWith("https://");
    let canOpen = isHttp;
    if (!isHttp) {
      try {
        canOpen = await Linking.canOpenURL(url);
      } catch {
        canOpen = false;
      }
    }
    if (canOpen) {
      try {
        await Linking.openURL(url);
        opened = true;
      } catch {
        /* fall through to clipboard */
      }
    }
  }
  if (opened && isPrefill) return "prefilled";
  await Clipboard.setStringAsync(copy_text);
  return opened ? "opened_and_copied" : "copied";
}
