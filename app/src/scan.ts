import * as ImagePicker from "expo-image-picker";
import type { BetIn, BetInDraft } from "./api/types";

export interface PickedImage {
  base64: string;
  mediaType: string;
}

/** Launch the camera or photo library and return a base64 image, or null on deny/cancel. */
export async function pickImage(source: "camera" | "library"): Promise<PickedImage | null> {
  const perm =
    source === "camera"
      ? await ImagePicker.requestCameraPermissionsAsync()
      : await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!perm.granted) return null;

  const res =
    source === "camera"
      ? await ImagePicker.launchCameraAsync({ base64: true, quality: 0.6 })
      : await ImagePicker.launchImageLibraryAsync({
          base64: true,
          quality: 0.6,
          mediaTypes: ImagePicker.MediaTypeOptions.Images,
        });

  const asset = res.canceled ? undefined : res.assets?.[0];
  if (!asset?.base64) return null;
  const mediaType = asset.mimeType ?? (asset.uri?.endsWith(".png") ? "image/png" : "image/jpeg");
  return { base64: asset.base64, mediaType };
}

/** Build a POST /bets body from a scan draft + the user's confirmed stake/odds/book. */
export function draftToBetIn(
  draft: BetInDraft,
  inputs: { stake?: number | null; fillOdds?: number | null; book?: string | null },
): BetIn {
  return {
    event_id: draft.event_id ?? "",
    sport: draft.sport,
    home: draft.home,
    away: draft.away,
    commence_time: draft.commence_time ?? null,
    market: draft.market ?? "",
    side: draft.side ?? "",
    point: draft.point,
    player: draft.player,
    label: draft.label,
    book: inputs.book ?? draft.book,
    rec_odds: draft.rec_odds,
    fill_odds: inputs.fillOdds ?? draft.fill_odds,
    stake: inputs.stake ?? draft.stake ?? null,
    source: draft.source || "scan",
  };
}
