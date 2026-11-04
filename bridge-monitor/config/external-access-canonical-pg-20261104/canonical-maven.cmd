@echo off
call "C:\ProgramData\chocolatey\lib\maven\apache-maven-3.9.12\bin\mvn.cmd" -f "D:\IdeaProjects\mri\mri-java-services-3\pom.xml" %*
exit /b %ERRORLEVEL%
