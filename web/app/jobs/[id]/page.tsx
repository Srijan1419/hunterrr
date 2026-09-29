import { Metadata } from "next";
import { auth } from "@/lib/auth/config";
import { headers } from "next/headers";
import { notFound } from "next/navigation";
import { queryJobById } from "@/lib/queries/jobs";
import { JobDetailClient } from "./JobDetailClient";

interface PageProps {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const resolvedParams = await params;
  return {
    title: `Job Details | Hunterrr`,
    description: "View full job details including extracted skills.",
  };
}

export default async function JobDetailPage({ params }: PageProps) {
  const session = await auth.api.getSession({
    headers: await headers(),
  });

  const resolvedParams = await params;
  const job = await queryJobById(resolvedParams.id);

  if (!job) {
    notFound();
  }

  return (
    <JobDetailClient
      job={job}
      userSignedIn={!!session}
    />
  );
}