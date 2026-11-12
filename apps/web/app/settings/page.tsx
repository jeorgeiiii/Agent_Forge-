import { redirect } from "next/navigation";

/** /settings opens the Team tab. */
export default function SettingsIndex() {
  redirect("/settings/team");
}
