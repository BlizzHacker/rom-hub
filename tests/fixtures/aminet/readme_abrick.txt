Short:        Tetris clone.
Author:       Milan Babuskov Amiga port by Ventzislav Tzvetkov
Uploader:     Ventzislav Tzvetkov
Type:         game/think
Version:      1.12
Architecture: ppc-amigaos >= 4.0.0

About game

 Graphics and programming by Milan Babuskov
Music by LifeSound

 The goal of the game is to reach the highest possible score. Shapes fall from
the top of the screen. You can move them sideways and rotate them in order to
get the best fit when they reach the bottom. When you manage to complete a
horizontal line, it disappears. When screen is full the game is over. The game
speeds up whenever the lines are full. Therefore, it's better to fill as many
lines you can at once. That way you can reach higher levels with the same
speed.

For single player game there are three game modes. In Classic mode you start
each level with clean screen. You get one point for each brick and:

 5 points for one line
20 points for two lines at once
45 points for three lines at once
80 points for four lines at once

In Challenge mode you start levels with some garbage at bottom, and you only
have two brick types (shapes) for the level. You get 3 points for each brick,
but lower bonuses for lines:

 3 points for one line
12 points for two lines at once
27 points for three lines at once
48 points for four lines at once


In Bastet mode you start levels with clean screen. You always get the worst
possible brick for the moment, and next brick preview is not available. You get
20 points for each brick, and high bonuses for lines:

100 points for one line
400 points for two lines at once
900 points for three lines at once
1600 points for four lines at once

Bastet mode is based on the Bastet game by Federico Poloni. The algorithm is
taken from Bastet 0.41. To quote: Bastet does not choose your next brick at
random. Instead, Bastet uses a special algorithm designed to choose the worst
brick possible. As you can imagine, playing Bastet can be a very frustrating
experience!

In two player games (duel), the goal is to remain the only one still playing.
When you drop more than one line, the other player gets additional garbage lines
at the bottom. For two lines he gets one, for three he gets two and for four he
gets three (in short: what you've dropped minus one).
