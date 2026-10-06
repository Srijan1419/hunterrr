import { redirect } from "next/navigation";

/** The front door: signed-in visitors land on Today (the route guard sends signed-out ones to sign-in). */
export default function HomePage() {
  redirect("/today");
}
