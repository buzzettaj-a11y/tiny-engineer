# The brain runs on the home PC, not on the robot

The ESP32-C3 can't recognise a wake phrase, transcribe speech or run a language model: it has too little memory and no AI acceleration. So the robot streams its mic over UDP to a brain on an always-on home PC, which does all listening, thinking and speaking and is the only thing that drives the robot. Claude Code hooks on every machine report to the brain, not the robot. The home PC must be reachable from the robot (a real PC or a bridged VM), which rules out a NAT'd VM.
