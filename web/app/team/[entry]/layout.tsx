"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useSession } from "@/lib/session";
import { SampleBanner } from "@/components/ui";

export default function TeamLayout({ children }: { children: React.ReactNode }) {
  const { entry } = useParams<{ entry: string }>();
  const router = useRouter();
  const { reset } = useSession();
  return (
    <div>
      {entry === "sample" && (
        <div className="mb-4">
          <SampleBanner />
        </div>
      )}
      <div className="mb-2 flex flex-wrap items-center justify-end gap-3 text-sm">
        <Link href="/" className="font-semibold text-pitch underline underline-offset-4">
          Change team
        </Link>
        <button
          type="button"
          className="font-semibold text-slate underline underline-offset-4 hover:text-kit"
          onClick={async () => {
            await reset();
            router.push("/");
          }}
        >
          Forget this team
        </button>
      </div>
      {children}
    </div>
  );
}
