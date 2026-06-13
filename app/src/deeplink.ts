// app/src/deeplink.ts
import * as Linking from "expo-linking";
import * as Clipboard from "expo-clipboard";
import type { Deeplink } from "./api/types";
import { VERIFIED_PREFILL_BOOKS } from "./config";

export function isVerifiedPrefill(book: string, dl: Deeplink): boolean {
  return dl.tier === "prefill" && VERIFIED_PREFILL_BOOKS.includes(book);
}

/** Open the server-built link via the system handler, falling back to clipboard.
 *  https universal links are always openable; custom schemes (dksb://) must pass
 *  canOpenURL, which THROWS on iOS when the scheme isn't whitelisted — treat a
 *  throw as "not installed" and copy instead. Never a dead end. */
export async function openOrCopy(dl: Deeplink): Promise<"opened" | "copied"> {
  const { url, copy_text } = dl;
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
        return "opened";
      } catch {
        /* fall through to clipboard */
      }
    }
  }
  await Clipboard.setStringAsync(copy_text);
  return "copied";
}
