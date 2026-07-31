# What Happens When You Build a Production App With AI as Your Co-Developer — Part 3

*Part 3 of a series on building a serverless photography marketplace on AWS. Parts 1 and 2 covered the architecture and the bugs. This article is about the process — specifically, what changes when AI is part of your engineering workflow from day one.*

---

I want to be honest about something before this article starts: I didn't set out to write about AI-assisted development. I set out to build a photography marketplace and study for my AWS certifications. The AI collaboration angle emerged from what actually happened over the course of the project — and it turned out to be the part worth writing about.

---

## How It Actually Worked

The workflow wasn't "describe what you want and watch code appear." That framing undersells what AI actually contributes and oversells what it replaces.

In practice, I worked with Claude as a technical collaborator throughout the project. Not as a code generator that I supervised, and not as a search engine I queried. More like a senior engineer who was available at any hour, had read every relevant AWS documentation page, and could look at my actual code and reason about it.

A typical session looked like this: I would describe what I was trying to build, share the relevant code or error output, ask a specific question, and then push back on the answer if it didn't match what I was seeing in the console. Claude would ask for more context — the exact error text, the CloudWatch log line, the HTTP response body — and then give a specific diagnosis rather than a generic suggestion.

The key word in that description is *specific*. The quality of the collaboration was directly proportional to the specificity of what I brought to it. A vague description of "it's not working" produced generic suggestions. A screenshot of the network tab showing a 401 with a specific error message produced the actual fix.

---

## What AI Does Well in This Context

**Generating boilerplate with real structure.** Writing the skeleton of a Lambda function — imports, handler signature, error handling, response formatting, CORS headers — is the same pattern repeated 37 times. AI handles that correctly and consistently. What took me a session to write for the first function took minutes for each subsequent one, with the same structure and naming conventions throughout.

**Explaining AWS service behavior from first principles.** When something doesn't work the way the documentation suggests, AI can reason about *why*. The Cognito callback routing bug in Article 2 — where the SDK routes `onSuccess` exceptions to `onFailure` — isn't prominently documented. It's a design decision that makes sense once explained, but isn't obvious from the error message. Getting a clear explanation of the mechanism changed how I debugged the rest of the project.

**Catching things I wasn't looking for.** The most valuable moments were when a review surfaced something I hadn't asked about. The IDOR vulnerability in `DeletePhotoFunction` — the security hole where any photographer could delete another's photos — appeared during a formal code review, not during development. I asked for a security audit and got back a finding I had genuinely missed. That's the kind of thing a code review partner is supposed to do.

**Holding context across a complex codebase.** A 2,600-line SAM template with 37 Lambda functions, 9 DynamoDB tables, and 4 CloudFront distributions is a lot to keep in working memory. Being able to ask "which functions have S3ReadPolicy for the thumbs bucket?" and get an accurate answer from the actual template — rather than from general knowledge — changed how quickly I could reason about the system.

---

## What AI Does Not Do

**Make architectural decisions for you.** Every major decision in this project was mine. Whether to use two separate Cognito user pools instead of groups. Whether to deploy multi-region from day one. Whether to use Step Functions for the pipeline or chain Lambda invocations directly. AI could explain the trade-offs clearly, but the judgment call was always mine — because the requirements were mine, and no one else knew them as well.

**Know when your requirements have changed.** AI has no way to know that a feature you described three weeks ago is now obsolete, or that a constraint you mentioned is no longer real. That context drift is entirely on the engineer. Several times during the project I had to actively correct course — to say "ignore what I said earlier about X, the approach changed" — because AI would otherwise continue working within a framing that was no longer accurate.

**Replace the discipline of actually understanding what you built.** This is the one that matters most. There were moments in this project where I could have accepted generated code, deployed it, and moved on without fully understanding it. I didn't, for two reasons. First, I was studying for AWS certifications — accepting code I couldn't explain would have defeated the entire purpose. Second, the bugs in Part 2 all required real understanding to diagnose. If you don't understand why your code works, you won't understand why it stopped.

---

## What the Collaboration Changed

The most concrete thing it changed was speed. Not speed of typing — I can type reasonably fast — but speed of moving from problem to solution. When something broke, I wasn't starting from a blank search bar. I had a collaborator who could look at the actual error, ask the right follow-up questions, and tell me specifically what to check.

That speed has a compounding effect when you're learning. Every debugging session in traditional development teaches you something. That doesn't disappear with AI assistance. What disappears is the time you spend searching for the right question to ask, reading through ten pages of documentation to find the two relevant paragraphs, and making changes that don't address the root cause. You still learn; you just spend that learning time on the right problem.

The less concrete thing it changed was confidence. A solo project has a natural failure mode: you hit a problem you don't know how to solve, you spend days stuck on it, you start to wonder if the whole thing is beyond your current level. That failure mode didn't happen in this project. Not because AI solved everything — there were plenty of things that required real work to figure out — but because there was always a next step. Always a question to ask, a hypothesis to test, a piece of code to review. That changes the psychological experience of building something difficult.

---

## What This Means for Learning AWS

I want to address this directly because it's the question I get asked most often: if you're using AI to write code, are you actually learning?

The honest answer is: it depends entirely on what you do with it.

If you generate code, deploy it, and move on — you learn almost nothing. The code works, but you have no idea why, and you'll have no ability to modify it, debug it, or extend it when the requirements change.

If you use generated code as a starting point, read it carefully, ask questions about the parts you don't understand, push back when the explanation doesn't match your mental model, and then test it against what you actually know about the service — you learn faster than you would otherwise.

The second approach is what I tried to do. I was actively studying for the AWS Solutions Architect Associate and Professional certifications throughout this project. Every time a Lambda function used a specific service, I wanted to understand not just how to configure it but *why* it behaves the way it does, what the exam would ask about it, and what a wrong answer looks like. That rigor came from me, not from the AI.

The certifications and the project complemented each other in a way I didn't expect. Building real infrastructure made exam questions concrete. When a question asks about the difference between an S3 bucket policy and an IAM policy, I have a specific example in my head from debugging an actual access denial. When a question asks about CloudFront OAC versus OAI, I've configured OAC and can explain exactly what it does. That kind of grounded understanding is harder to get from courses alone.

---

## The Honest Assessment

Building Galleria with AI assistance produced a better application faster than I would have built alone. The architecture is more complete, the security posture is stronger, and the feature set is more ambitious than what I would have attempted solo. That's the upside, and it's real.

The downside is that it requires active discipline to keep it from becoming a shortcut that produces code you don't understand. That discipline is the engineer's job — AI won't enforce it for you.

What I came away from this project believing is that AI-assisted development isn't easier than traditional development. It's different. The hard part shifts from "how do I write this?" to "how do I understand what was written, validate that it's correct, and extend it when requirements change?" Those are still engineering skills. They're just slightly different ones.

For anyone early in their AWS journey: build something real. Use whatever tools help you build it. But insist on understanding every piece of it — because the day something breaks in production, no tool will debug it for you.

---

*The full source code is on GitHub at [github.com/jvhammond2/serverless-photo-galleria](https://github.com/jvhammond2/serverless-photo-galleria). The architecture overview is in Part 1. The debugging war stories are in Part 2.*

---

*Joel is a cloud developer and AWS Solutions Architect candidate building production-grade serverless applications. He is a selected developer on the Digital Cloud Training collaborative program.*
