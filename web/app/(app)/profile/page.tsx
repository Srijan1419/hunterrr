import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Profile | hunterrr",
  description: "Your skills, locations and pay floor — what decides how jobs are ranked.",
};

export default function ProfilePage() {
  return (
    <div>
      <h1>Profile</h1>
      <p>Your skills, locations and pay floor will appear here and decide how jobs are ranked.</p>
      <p>Nothing here yet — your profile details will show up here.</p>
    </div>
  );
}
