import { redirect } from "next/navigation";

/** Sign-up is disabled (one Google account only); anyone landing here goes to sign-in. */
export default function SignupPage() {
  redirect("/signin");
}
