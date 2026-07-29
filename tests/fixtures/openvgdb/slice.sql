-- A slice of OpenVGDB v29.0 (openvgdb.sqlite, SHA-1 of the release
-- asset's contents unchanged since 2021-11-10), captured 2026-07-29.
-- Schema statements are verbatim from the file's own sqlite_master.
-- Rows are verbatim; only the selection of them is ours.
CREATE TABLE "SYSTEMS" (
"systemID" INTEGER PRIMARY KEY AUTOINCREMENT,
"systemName" TEXT,
"systemShortName" TEXT,
"systemHeaderSizeBytes" INTEGER,
"systemHashless" INTEGER,
"systemHeader" INTEGER,
"systemSerial" TEXT,
"systemOEID" TEXT
);
CREATE TABLE "REGIONS" (
"regionID" INTEGER PRIMARY KEY AUTOINCREMENT,
"regionName" TEXT
);
CREATE TABLE "ROMs" (
"romID" INTEGER PRIMARY KEY AUTOINCREMENT,
"systemID" INTEGER,
"regionID" INTEGER,
"romHashCRC" TEXT,
"romHashMD5" TEXT,
"romHashSHA1" TEXT,
"romSize" INTEGER,
"romFileName" TEXT,
"romExtensionlessFileName" TEXT,
"romParent" TEXT,
"romSerial" TEXT,
"romHeader" TEXT,
"romLanguage" TEXT,
"TEMPromRegion" TEXT,
"romDumpSource" TEXT
);
CREATE TABLE "RELEASES" (
"releaseID" INTEGER PRIMARY KEY AUTOINCREMENT,
"romID" INTEGER,
"releaseTitleName" TEXT,
"regionLocalizedID" INTEGER,
"TEMPregionLocalizedName" TEXT,
"TEMPsystemShortName" TEXT,
"TEMPsystemName" TEXT,
"releaseCoverFront" TEXT,
"releaseCoverBack" TEXT,
"releaseCoverCart" TEXT,
"releaseCoverDisc" TEXT,
"releaseDescription" TEXT,
"releaseDeveloper" TEXT,
"releasePublisher" TEXT,
"releaseGenre" TEXT,
"releaseDate" TEXT,
"releaseReferenceURL" TEXT,
"releaseReferenceImageURL" TEXT
);

INSERT INTO "SYSTEMS" ("systemID", "systemName", "systemShortName", "systemHeaderSizeBytes", "systemHashless", "systemHeader", "systemSerial", "systemOEID") VALUES (2, 'Arcade', 'MAME', NULL, 1, NULL, NULL, 'openemu.system.arcade');
INSERT INTO "SYSTEMS" ("systemID", "systemName", "systemShortName", "systemHeaderSizeBytes", "systemHashless", "systemHeader", "systemSerial", "systemOEID") VALUES (19, 'Nintendo Game Boy', 'GB', NULL, NULL, NULL, NULL, 'openemu.system.gb');
INSERT INTO "SYSTEMS" ("systemID", "systemName", "systemShortName", "systemHeaderSizeBytes", "systemHashless", "systemHeader", "systemSerial", "systemOEID") VALUES (22, 'Nintendo GameCube', 'NGC', NULL, NULL, NULL, '1', 'openemu.system.gc');
INSERT INTO "SYSTEMS" ("systemID", "systemName", "systemShortName", "systemHeaderSizeBytes", "systemHashless", "systemHeader", "systemSerial", "systemOEID") VALUES (33, 'Sega Genesis/Mega Drive', 'MD', NULL, NULL, NULL, NULL, 'openemu.system.sg');

INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (1, 'Asia');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (2, 'Australia');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (3, 'Brazil');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (4, 'Canada');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (5, 'China');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (6, 'Denmark');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (7, 'Europe');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (8, 'Finland');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (9, 'France');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (10, 'Germany');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (11, 'Hong Kong');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (12, 'Italy');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (13, 'Japan');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (14, 'Korea');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (15, 'Netherlands');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (16, 'Russia');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (17, 'Spain');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (18, 'Sweden');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (19, 'Taiwan');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (20, 'Unknown');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (21, 'USA');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (22, 'World');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (23, 'Asia, Australia');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (24, 'Brazil, Korea');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (25, 'Japan, Europe');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (26, 'Japan, Korea');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (27, 'Japan, USA');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (28, 'USA, Australia');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (29, 'USA, Europe');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (30, 'USA, Korea');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (31, 'Europe, Australia');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (32, 'Greece');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (33, 'Ireland');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (34, 'Norway');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (35, 'Portugal');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (36, 'Scandinavia');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (37, 'UK');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (38, 'USA, Brazil');
INSERT INTO "REGIONS" ("regionID", "regionName") VALUES (39, 'Poland');

INSERT INTO "ROMs" ("romID", "systemID", "regionID", "romHashCRC", "romHashMD5", "romHashSHA1", "romSize", "romFileName", "romExtensionlessFileName", "romParent", "romSerial", "romHeader", "romLanguage", "TEMPromRegion", "romDumpSource") VALUES (4230, 19, 22, '46DF91AD', '982ED5D2B12A0377EB14BCDC4123744E', '74591CC9501AF93873F9A5D3EB12DA12C0723BBC', 32768, 'Tetris (World) (Rev A).gb', 'Tetris (World) (Rev A)', NULL, NULL, NULL, NULL, 'World', 'No-Intro');
INSERT INTO "ROMs" ("romID", "systemID", "regionID", "romHashCRC", "romHashMD5", "romHashSHA1", "romSize", "romFileName", "romExtensionlessFileName", "romParent", "romSerial", "romHeader", "romLanguage", "TEMPromRegion", "romDumpSource") VALUES (4231, 19, 29, 'EA19A9D9', '0A2E27E279EE4FAAC326B0CF620B269B', '1F88BE9DEA864EAA74C3B5CE3488E61139818E8D', 131072, 'Tetris 2 (USA, Europe) (SGB Enhanced).gb', 'Tetris 2 (USA, Europe) (SGB Enhanced)', NULL, NULL, NULL, NULL, 'USA, Europe', 'No-Intro');
INSERT INTO "ROMs" ("romID", "systemID", "regionID", "romHashCRC", "romHashMD5", "romHashSHA1", "romSize", "romFileName", "romExtensionlessFileName", "romParent", "romSerial", "romHeader", "romLanguage", "TEMPromRegion", "romDumpSource") VALUES (15920, 33, 29, '154D59BB', '2ED5293E46ABE1E74EDEB96DA0E7A618', '38945360D824D2FB9535B4FD7F25B9AA9B32F019', 524288, 'Altered Beast (USA, Europe).md', 'Altered Beast (USA, Europe)', NULL, NULL, NULL, NULL, 'USA, Europe', 'No-Intro');
INSERT INTO "ROMs" ("romID", "systemID", "regionID", "romHashCRC", "romHashMD5", "romHashSHA1", "romSize", "romFileName", "romExtensionlessFileName", "romParent", "romSerial", "romHeader", "romLanguage", "TEMPromRegion", "romDumpSource") VALUES (17516, 2, 20, NULL, NULL, NULL, NULL, '005.zip', '005', '', NULL, NULL, NULL, 'Unknown', 'MAME');
INSERT INTO "ROMs" ("romID", "systemID", "regionID", "romHashCRC", "romHashMD5", "romHashSHA1", "romSize", "romFileName", "romExtensionlessFileName", "romParent", "romSerial", "romHeader", "romLanguage", "TEMPromRegion", "romDumpSource") VALUES (84282, 22, 10, '43C1D6A0', 'A91E2B43028EEE80F6E953A542DEE9C9', '34D610E08042B896FC5C077E6E7060FC00B77AA4', 1459978240, '007 - Agent im Kreuzfeuer (Germany).iso', '007 - Agent im Kreuzfeuer (Germany)', NULL, 'GW7D69', NULL, 'DE', 'Germany', 'Redump');

INSERT INTO "RELEASES" ("releaseID", "romID", "releaseTitleName", "regionLocalizedID", "TEMPregionLocalizedName", "TEMPsystemShortName", "TEMPsystemName", "releaseCoverFront", "releaseCoverBack", "releaseCoverCart", "releaseCoverDisc", "releaseDescription", "releaseDeveloper", "releasePublisher", "releaseGenre", "releaseDate", "releaseReferenceURL", "releaseReferenceImageURL") VALUES (48399, 4230, 'Tetris', 7, 'Europe', 'GB', 'Nintendo Game Boy', 'https://gamefaqs.gamespot.com/a/box/2/8/3/22283_front.jpg', 'https://gamefaqs.gamespot.com/a/box/2/8/3/22283_back.jpg', NULL, NULL, 'The Soviet game sensation is now on your Game Boy! Beams, boxes, zig-zags and L-shaped blocks drop down a narrow passage. Feel your pulse quicken as you spin, shift and align the shapes for a perfect fit. It''s challenging and demands split-second decision making! Start at new heights for a tougher contest. Pick the music and set your pace from 20 progressive skill levels!', 'Bullet Proof Software', NULL, 'Miscellaneous,Puzzle,Stacking', 'June 1989', 'http://www.gamefaqs.com/gameboy/585960-tetris', 'http://www.gamefaqs.com/gameboy/585960-tetris/images/box-68396');
INSERT INTO "RELEASES" ("releaseID", "romID", "releaseTitleName", "regionLocalizedID", "TEMPregionLocalizedName", "TEMPsystemShortName", "TEMPsystemName", "releaseCoverFront", "releaseCoverBack", "releaseCoverCart", "releaseCoverDisc", "releaseDescription", "releaseDeveloper", "releasePublisher", "releaseGenre", "releaseDate", "releaseReferenceURL", "releaseReferenceImageURL") VALUES (48400, 4230, 'Tetris', 13, 'Japan', 'GB', 'Nintendo Game Boy', 'https://gamefaqs.gamespot.com/a/box/2/8/1/22281_front.jpg', 'https://gamefaqs.gamespot.com/a/box/2/8/1/22281_back.jpg', NULL, NULL, 'The Soviet game sensation is now on your Game Boy! Beams, boxes, zig-zags and L-shaped blocks drop down a narrow passage. Feel your pulse quicken as you spin, shift and align the shapes for a perfect fit. It''s challenging and demands split-second decision making! Start at new heights for a tougher contest. Pick the music and set your pace from 20 progressive skill levels!', 'Bullet Proof Software', NULL, 'Miscellaneous,Puzzle,Stacking', 'June 1989', 'http://www.gamefaqs.com/gameboy/585960-tetris', 'http://www.gamefaqs.com/gameboy/585960-tetris/images/box-2883');
INSERT INTO "RELEASES" ("releaseID", "romID", "releaseTitleName", "regionLocalizedID", "TEMPregionLocalizedName", "TEMPsystemShortName", "TEMPsystemName", "releaseCoverFront", "releaseCoverBack", "releaseCoverCart", "releaseCoverDisc", "releaseDescription", "releaseDeveloper", "releasePublisher", "releaseGenre", "releaseDate", "releaseReferenceURL", "releaseReferenceImageURL") VALUES (48401, 4230, 'Tetris', 21, 'USA', 'GB', 'Nintendo Game Boy', 'https://gamefaqs.gamespot.com/a/box/2/8/2/22282_front.jpg', 'https://gamefaqs.gamespot.com/a/box/2/8/2/22282_back.jpg', NULL, NULL, 'The Soviet game sensation is now on your Game Boy! Beams, boxes, zig-zags and L-shaped blocks drop down a narrow passage. Feel your pulse quicken as you spin, shift and align the shapes for a perfect fit. It''s challenging and demands split-second decision making! Start at new heights for a tougher contest. Pick the music and set your pace from 20 progressive skill levels!', 'Bullet Proof Software', NULL, 'Miscellaneous,Puzzle,Stacking', 'June 1989', 'http://www.gamefaqs.com/gameboy/585960-tetris', 'http://www.gamefaqs.com/gameboy/585960-tetris/images/box-38759');
INSERT INTO "RELEASES" ("releaseID", "romID", "releaseTitleName", "regionLocalizedID", "TEMPregionLocalizedName", "TEMPsystemShortName", "TEMPsystemName", "releaseCoverFront", "releaseCoverBack", "releaseCoverCart", "releaseCoverDisc", "releaseDescription", "releaseDeveloper", "releasePublisher", "releaseGenre", "releaseDate", "releaseReferenceURL", "releaseReferenceImageURL") VALUES (48402, 4231, 'Tetris 2', 7, 'Europe', 'GB', 'Nintendo Game Boy', 'https://gamefaqs.gamespot.com/a/box/3/0/5/25305_front.jpg', 'https://gamefaqs.gamespot.com/a/box/3/0/5/25305_back.jpg', NULL, NULL, 'The mesmerizing fun of Tetris returns - and the challenge escalates to new heights! Test your dexterity, tease your brain and rack up points with Tetris 2. Your split-second decisions lead you to a new dimension in puzzle-solving action! Play alone or challenge a friend in simultaneous split-screen action. A tougher Tetris with more shapes, more components, 30 levels - and unlimited solutions! If you loved the international game sensation Tetris, you''ll be wild for the newest dimension in puzzle fun: Tetris 2!', 'TOSE', NULL, 'Miscellaneous,Puzzle,Stacking', 'December 1993', 'http://www.gamefaqs.com/gameboy/585961-tetris-2', 'http://www.gamefaqs.com/gameboy/585961-tetris-2/images/box-60607');
INSERT INTO "RELEASES" ("releaseID", "romID", "releaseTitleName", "regionLocalizedID", "TEMPregionLocalizedName", "TEMPsystemShortName", "TEMPsystemName", "releaseCoverFront", "releaseCoverBack", "releaseCoverCart", "releaseCoverDisc", "releaseDescription", "releaseDeveloper", "releasePublisher", "releaseGenre", "releaseDate", "releaseReferenceURL", "releaseReferenceImageURL") VALUES (48403, 4231, 'Tetris 2', 21, 'USA', 'GB', 'Nintendo Game Boy', 'https://gamefaqs.gamespot.com/a/box/3/0/4/25304_front.jpg', 'https://gamefaqs.gamespot.com/a/box/3/0/4/25304_back.jpg', NULL, NULL, 'The mesmerizing fun of Tetris returns - and the challenge escalates to new heights! Test your dexterity, tease your brain and rack up points with Tetris 2. Your split-second decisions lead you to a new dimension in puzzle-solving action! Play alone or challenge a friend in simultaneous split-screen action. A tougher Tetris with more shapes, more components, 30 levels - and unlimited solutions! If you loved the international game sensation Tetris, you''ll be wild for the newest dimension in puzzle fun: Tetris 2!', 'TOSE', NULL, 'Miscellaneous,Puzzle,Stacking', 'December 1993', 'http://www.gamefaqs.com/gameboy/585961-tetris-2', 'http://www.gamefaqs.com/gameboy/585961-tetris-2/images/box-38760');
INSERT INTO "RELEASES" ("releaseID", "romID", "releaseTitleName", "regionLocalizedID", "TEMPregionLocalizedName", "TEMPsystemShortName", "TEMPsystemName", "releaseCoverFront", "releaseCoverBack", "releaseCoverCart", "releaseCoverDisc", "releaseDescription", "releaseDeveloper", "releasePublisher", "releaseGenre", "releaseDate", "releaseReferenceURL", "releaseReferenceImageURL") VALUES (54428, 15920, 'Altered Beast', 7, 'Europe', 'MD', 'Sega Genesis/Mega Drive', 'https://gamefaqs.gamespot.com/a/box/7/2/0/22720_front.jpg', 'https://gamefaqs.gamespot.com/a/box/7/2/0/22720_back.jpg', NULL, NULL, 'Enter a time when men were warriors and Gods ruled the world. A time of good against evil, a place of danger. Summoned by Zeus to rescue Athena, you will infiltrate the Underworld with the power to transform into mythical creatures with supernatural strength. Level 1: Become a savage Werewolf and use teeth and nails to shred your enemies to pieces. Capture 3 of the elusive Spirit Balls and you''ll be transformed into a ferocious, fireball throwing Werewolf. Levels 2 & 3: Take flight as Weredragon and use fiery force to fry the followers of Neff. Creep across slippery crevasses inside a deep cavern as the crafty Werebear. Levels 4 & 5: Stalk the gates of the Underworld fortress as a man-eating Weretiger, a predator with no pity. Inside the inner sanctum call on Golden Werewolf''s might to demolish Neff the demon - forever!', 'Sega', NULL, 'Action,Beat-''Em-Up', 'Aug 14, 1989', 'http://www.gamefaqs.com/genesis/586022-altered-beast', 'http://www.gamefaqs.com/genesis/586022-altered-beast/images/box-88171');
INSERT INTO "RELEASES" ("releaseID", "romID", "releaseTitleName", "regionLocalizedID", "TEMPregionLocalizedName", "TEMPsystemShortName", "TEMPsystemName", "releaseCoverFront", "releaseCoverBack", "releaseCoverCart", "releaseCoverDisc", "releaseDescription", "releaseDeveloper", "releasePublisher", "releaseGenre", "releaseDate", "releaseReferenceURL", "releaseReferenceImageURL") VALUES (54429, 15920, 'Altered Beast', 21, 'USA', 'MD', 'Sega Genesis/Mega Drive', 'https://gamefaqs.gamespot.com/a/box/7/1/6/22716_front.jpg', 'https://gamefaqs.gamespot.com/a/box/7/1/6/22716_back.jpg', NULL, NULL, 'Enter a time when men were warriors and Gods ruled the world. A time of good against evil, a place of danger. Summoned by Zeus to rescue Athena, you will infiltrate the Underworld with the power to transform into mythical creatures with supernatural strength. Level 1: Become a savage Werewolf and use teeth and nails to shred your enemies to pieces. Capture 3 of the elusive Spirit Balls and you''ll be transformed into a ferocious, fireball throwing Werewolf. Levels 2 & 3: Take flight as Weredragon and use fiery force to fry the followers of Neff. Creep across slippery crevasses inside a deep cavern as the crafty Werebear. Levels 4 & 5: Stalk the gates of the Underworld fortress as a man-eating Weretiger, a predator with no pity. Inside the inner sanctum call on Golden Werewolf''s might to demolish Neff the demon - forever!', 'Sega', NULL, 'Action,Beat-''Em-Up', 'Aug 14, 1989', 'http://www.gamefaqs.com/genesis/586022-altered-beast', 'http://www.gamefaqs.com/genesis/586022-altered-beast/images/box-51618');
INSERT INTO "RELEASES" ("releaseID", "romID", "releaseTitleName", "regionLocalizedID", "TEMPregionLocalizedName", "TEMPsystemShortName", "TEMPsystemName", "releaseCoverFront", "releaseCoverBack", "releaseCoverCart", "releaseCoverDisc", "releaseDescription", "releaseDeveloper", "releasePublisher", "releaseGenre", "releaseDate", "releaseReferenceURL", "releaseReferenceImageURL") VALUES (65485, 17516, '005', 20, 'Unknown', 'MAME', 'Arcade', 'https://raw.githubusercontent.com/clobber/arcade-titles/master/005.png', NULL, NULL, NULL, NULL, 'Sega', NULL, NULL, '1981', NULL, NULL);
INSERT INTO "RELEASES" ("releaseID", "romID", "releaseTitleName", "regionLocalizedID", "TEMPregionLocalizedName", "TEMPsystemShortName", "TEMPsystemName", "releaseCoverFront", "releaseCoverBack", "releaseCoverCart", "releaseCoverDisc", "releaseDescription", "releaseDeveloper", "releasePublisher", "releaseGenre", "releaseDate", "releaseReferenceURL", "releaseReferenceImageURL") VALUES (165271, 84282, '007: Agent im Kreuzfeuer', 10, 'Germany', 'NGC', 'Nintendo GameCube', 'https://art.gametdb.com/wii/cover/DE/GW7D69.png', NULL, NULL, NULL, NULL, 'Electronic Arts ', 'Electronic Arts', 'action,adventure,first-person shooter', '2002/6/14', NULL, NULL);
