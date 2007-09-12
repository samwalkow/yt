"""
Python-based grid handler, not to be confused with the SWIG-handler

@author: U{Matthew Turk<http://www.stanford.edu/~mturk/>}
@organization: U{KIPAC<http://www-group.slac.stanford.edu/KIPAC/>}
@contact: U{mturk@slac.stanford.edu<mailto:mturk@slac.stanford.edu>}
@license:
  Copyright (C) 2007 Matthew Turk.  All Rights Reserved.

  This file is part of yt.

  yt is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation; either version 3 of the License, or
  (at your option) any later version.
  
  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.
  
  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

from yt.lagos import *
import yt.enki, gc
from yt.funcs import *

class EnzoGridBase:
    """
    Class representing a single Enzo Grid instance
    """
    def __init__(self, id, filename=None, hierarchy = None):
        """
        Returns an instance of EnzoGrid

        @param hierarchy: EnzoHierarchy, parent hierarchy
        @type hierarchy: L{EnzoHierarchy<EnzoHierarchy>}
        @param id: grid ID (NOT index, which is ID-1)
        @type id: int
        @keyword filename: filename holding grid data
        @type filename: string
        """
        self.id = id
        self.data = {}
        self.datasets = {}
        if hierarchy: self.hierarchy = hierarchy
        if filename: self.setFilename(filename)
        self.myOverlapMasks = [None, None, None]
        self.myOverlapGrids = [None, None, None]

    def __len__(self):
        return 0

    def __getitem__(self, key):
        """
        Returns a field or set of fields for a key or set of keys
        """
        if isinstance(key, types.StringType):
            if self.data.has_key(key):
                return self.data[key]
            else:
                self.readDataFast(key)
                return self.data[key]
        elif isinstance(key, types.ListType) or \
             isinstance(key, types.TupleType):
            tr = []
            for k in key:
                if self.data.has_key(k):
                    tr.append(self.data[k])
                else:
                    self.readDataFast(k)
                    tr.append(self.data[k])
            return tr
        else:
            return self.data[key]

    def __setitem__(self, key, data):
        """
        Sets a data field equal to some value or set of values
        """
        if isinstance(key, types.StringType):
            self.data[key] = data
        elif isinstance(key, types.ListType) or \
             isinstance(key, types.TupleType):
            tr = []
            for kI in range(len(key)):
                self.data[kI] = data[kI]
        else:
            self.data[key] = data

    def has_key(self, key):
        """
        Checks to see if this field *is already generated.*  Will not check to
        see if it *can* be generated.
        """
        return self.data.has_key(key)

    def keys(self):
        """
        Returns all existing fields.
        """
        return self.data.keys()

    def clearAllGridReferences(self):
        self.clearDerivedQuantities()
        if hasattr(self, 'hierarchy'):
            del self.hierarchy
        if hasattr(self, 'Parent'):
            if self.Parent != None:
                self.Parent.clearAllGridReferences()
            del self.Parent
        if hasattr(self, 'Children'):
            for i in self.Children:
                if i != None:
                    del i
            del self.Children

    def prepareGrid(self):
        """
        Copies all the appropriate attributes from the hierarchy
        """
        # This is definitely the slowest part of generating the hierarchy
        # Now we give it pointers to all of its attributes
        h = self.hierarchy # cache it
        self.Dimensions = h.gridDimensions[self.id-1]
        self.StartIndices = h.gridStartIndices[self.id-1]
        self.EndIndices = h.gridEndIndices[self.id-1]
        self.LeftEdge = h.gridLeftEdge[self.id-1]
        self.RightEdge = h.gridRightEdge[self.id-1]
        self.Level = h.gridLevels[self.id-1,0]
        self.Time = h.gridTimes[self.id-1,0]
        self.NumberOfParticles = h.gridNumberOfParticles[self.id-1,0]
        self.ActiveDimensions = (self.EndIndices - self.StartIndices + 1)
        self.Children = h.gridTree[self.id-1]
        pID = h.gridReverseTree[self.id-1]
        if pID != None and pID != -1:
            self.Parent = h.grids[pID - 1]
        else:
            self.Parent = None
        # So first we figure out what the index is.  We don't assume
        # that dx=dy=dz , at least here.  We probably do elsewhere.
        self.dx = (self.RightEdge[0] - self.LeftEdge[0]) / \
                  (self.EndIndices[0]-self.StartIndices[0]+1)
        self.dy = (self.RightEdge[1] - self.LeftEdge[1]) / \
                  (self.EndIndices[1]-self.StartIndices[1]+1)
        self.dz = (self.RightEdge[2] - self.LeftEdge[2]) / \
                  (self.EndIndices[2]-self.StartIndices[2]+1)
        h.gridDxs[self.id-1,0] = self.dx
        if ytcfg.getboolean("lagos","ReconstructHierarchy") == True:
            if self.Parent == None: return
            # Okay, we're going to try to guess
            # We know that our grid boundary occurs on the cell boundary of our
            # parent
            le = self.LeftEdge
            self.dx = self.Parent.dx/2.0
            self.dy = self.Parent.dy/2.0
            self.dz = self.Parent.dz/2.0
            ParentLeftIndex = na.rint((self.LeftEdge-self.Parent.LeftEdge)/self.Parent.dx)
            self.LeftEdge = self.Parent.LeftEdge + self.Parent.dx * ParentLeftIndex
            self.RightEdge = self.LeftEdge + self.ActiveDimensions*self.dx
            #if self.Level > 20: print "Recon", (self.LeftEdge-le)/self.dx

    #@time_execution
    def generateOverlapMasks(self, axis, LE, RE):
        """
        Generate a mask that shows which cells overlap with other cells on
        different grids.  (If fed appropriate subsets, can be constrained to
        current level.
        Use algorithm described at http://www.gamedev.net/reference/articles/article735.asp

        @param axis: axis  along which line of sight is drawn
        @type axis: int
        @param LE: LeftEdge positions to check against
        @type LE: array of floats
        @param RE: RightEdge positions to check against
        @type RE: array of floats
        """
        x = x_dict[axis]
        y = y_dict[axis]
        cond1 = self.RightEdge[x] > LE[:,x]
        cond2 = self.LeftEdge[x] < RE[:,x]
        cond3 = self.RightEdge[y] > LE[:,y]
        cond4 = self.LeftEdge[y] < RE[:,y]
        self.myOverlapMasks[axis]=na.logical_and(na.logical_and(cond1, cond2), \
                                                 na.logical_and(cond3, cond4))
    def __repr__(self):
        return "%s" % (self.id)

    def __int__(self):
        return self.id

    def setFilename(self, filename):
        if filename[0] == os.path.sep:
            self.filename = filename
        else:
            self.filename = os.path.join(self.hierarchy.directory, filename)
        return

    def findMax(self, field):
        """
        Returns value, coordinate of maximum value in this gird

        @param field: field to check
        @type field: string
        """
        coord=nd.maximum_position(self[field])
        val = self[field][coord]
        return val, coord

    def findMin(self, field):
        """
        Returns value, coordinate of minimum value in this gird

        @param field: field to check
        @type field: string
        """
        coord=nd.minimum_position(self[field])
        val = self[field][coord]
        return val, coord

    def getPosition(self, coord):
        """
        Returns position of a coordinate

        @param coord: position to check
        @type coord: array of floats
        """
        pos = (coord + 0.0) * self.dx + self.LeftEdge
        # Should 0.0 be 0.5?
        return pos

    def clearAll(self):
        """
        Clears all datafields from memory.
        """
        for key in self.keys():
            del self.data[key]
        del self.data
        if hasattr(self,"retVal"):
            del self.retVal
        self.data = {}
        self.clearDerivedQuantities()

    def clearDerivedQuantities(self):
        """
        Clears coordinates, myChildIndices, myChildMask.
        """
        # Access the property raw-values here
        del self.myChildMask
        del self.myChildIndices
        del self.coords

    def generateField(self, fieldName):
        """
        Generates, or attempts to generate,  a field not found in the file

        See DerivedFields.py for more information.  fieldInfo.keys() will list all of
        the available derived fields.  Note that we also make available the
        suffices _Fraction and _Squared here.  All fields prefixed with 'k'
        will force an attempt to use the chemistry tables to generate them from
        temperature.  All fields used in generation will remain resident in
        memory.

        I feel like there's a reason that EnzoGrid isn't subclassed from
        EnzoData, and I think it's related to this method.  But I can't remember now.

        @param fieldName: field name
        @type fieldName: string

        """
        # This is for making derived fields
        # Note that all fields used for derivation are kept resident in memory: probably a 
        # mistake, but it is expensive to do a lookup.  I will fix this later.
        #
        # Note that you can do a couple things: the suffices _Fraction and
        # _Squared will be dealt with appropriately.  Not sure what else to
        # add.
        if fieldName.endswith("Fraction"):
            # Very simple mass fraction here.  Could be modified easily,
            # but that would require a dict lookup, which is expensive, or
            # an elif block, which is inelegant
            baryonField = "%s_Density" % (fieldName[:-9])
            self[fieldName] = self[baryonField] / self["Density"]
        elif fieldName.endswith("Squared"):
            baryonField = fieldName[:-7]
            self[fieldName] = (self[baryonField])**2.0
        elif fieldName.endswith("_vcomp"):
            baryonField = fieldName[:-8]
            index = int(fieldName[-7:-6])
            self[fieldName] = self[baryonField][index,:]
        elif fieldName.endswith("_tcomp"):
            baryonField = fieldName[:-9]
            ii = map(int, fieldName[-8:-6])
            self[fieldName] = self[baryonField][ii[0],ii[1],:]
        elif fieldName.endswith("Abs"):
            baryonField = fieldName[:-4]
            self[fieldName] = abs(self[baryonField])
        elif fieldInfo.has_key(fieldName):
            # We do a fallback to checking the fieldInfo dict
            # Note that it'll throw an exception here if it's not found...
            # ...which I'm cool with
            fieldInfo[fieldName][3](self, fieldName)
        elif fieldName.startswith("k"):
            self[fieldName] = abs(self.hierarchy.rates[self["Temperature"],fieldName])
        else:
            raise exceptions.KeyError, fieldName

    def getEnzoGrid(self):
        """
        This attempts to get an instance of this particular grid from the SWIG
        interface.  Note that it first checks to see if the ParameterFile has
        been instantiated.
        """
        if self.hierarchy.eiTopGrid == None:
            self.hierarchy.initializeEnzoInterface()
        p=re.compile("Grid = %s\n" % (self.id))
        h=open(self.hierarchyFilename,"r").read()
        m=re.search(p,h)
        h=open(self.hierarchyFilename,"r")
        retVal = yt.enki.EnzoInterface.fseek(h, long(m.end()), 0)
        self.eiGrid=yt.enki.EnzoInterface.grid()
        cwd = os.getcwd() # Hate doing this, need to for relative pathnames
        os.chdir(self.hierarchy.directory)
        self.eiGrid.ReadGrid(h, 1)
        os.chdir(cwd)
        mylog.debug("Grid read with SWIG")

    def exportAmira(self, filename, fields, timestep = 1, a5Filename=None, gid=0):
        if (not iterable(fields)) or (isinstance(fields, types.StringType)):
            fields = [fields]
        deltas = na.array([self.dx,self.dy,self.dz],dtype=nT.Float64)
        tn = "time-%i" % (timestep)
        ln = "level-%i" % (self.Level)
        for field in fields:
            iorigin = (self.LeftEdge/deltas).astype(nT.Int64)
            new_h5 = tables.openFile(filename % {'field' : field}, "a")
            f = self[field].transpose().reshape(self.ActiveDimensions)
            new_h5.createArray("/","grid-%i" % (self.id), f)
            del f
            node = new_h5.getNode("/","grid-%i" % (self.id))
            node.setAttr("level",self.Level)
            node.setAttr("timestep",timestep)
            node.setAttr("time",self.Time)
            node.setAttr("cctk_bbox",na.array([0,0,0,0,0,0],dtype=nT.Int32))
            node.setAttr("cctk_nghostzones",na.array([0,0,0],dtype=nT.Int32))
            node.setAttr("delta",deltas)
            node.setAttr("origin",self.LeftEdge)
            node.setAttr("iorigin",iorigin*(2**(self.hierarchy.maxLevel - self.Level)))
            new_h5.close()
            if a5Filename != None:
                new_h5 = tables.openFile(a5Filename % {'field' : field}, "a")
                new_h5.createGroup("/%s/%s" % (tn, ln),"grid-%i" % (gid))
                node=new_h5.getNode("/%s/%s" % (tn, ln),"grid-%i" % (gid))
                node._f_setAttr("dims",self.ActiveDimensions)
                node._f_setAttr("ghostzoneFlags",na.array([0,0,0,0,0,0],dtype=nT.Int32))
                node._f_setAttr("integerOrigin",(self.LeftEdge/deltas).astype(nT.Int64))
                node._f_setAttr("numGhostzones",na.array([0,0,0],dtype=nT.Int32))
                node._f_setAttr("origin",self.LeftEdge)
                node._f_setAttr("referenceDataPath","/"+"grid-%i" % (self.id))
                fn = os.path.basename(filename % {'field' : field})
                node._f_setAttr("referenceFileName", fn)
                new_h5.close()

    def getProjection(self, axis, field, zeroOut, weight=None, func=na.sum):
        """
        Projects along an axis.  Currently in flux.  Shouldn't be called
        directly.
        """
        if weight == None:
            maskedData = self[field].copy()
            weightData = na.ones(maskedData.shape)
        else:
            maskedData = self[field] * self[weight]
            weightData = self[weight].copy()
        if len(self.myOverlapMasks) == 0:
            self.generateOverlapMasks()
        if zeroOut:
            maskedData[self.myChildIndices]=0
            weightData[self.myChildIndices]=0
            toCombineMask = na.logical_and.reduce(self.myChildMask, axis).astype(nT.Int64)
        # How do we do this the fastest?
        # We only want to project those values that don't have subgrids
        fullProj = func(maskedData,axis)*self.dx # Gives correct shape
        weightProj = func(weightData,axis)*self.dx
        if not zeroOut:
            toCombineMask = na.ones(fullProj.shape, dtype=nT.Int64)
        toCombineMask = toCombineMask.astype(nT.Int64)
        cmI = na.indices(fullProj.shape)
        xind = cmI[0,:].ravel()
        yind = cmI[1,:].ravel()
        a = {0:self.dx, 1:self.dy, 2:self.dz}
        dx = a[x_dict[axis]]
        dy = a[y_dict[axis]]
        xpoints = xind + na.rint(self.LeftEdge[x_dict[axis]]/dx).astype(nT.Int64)
        ypoints = yind + na.rint(self.LeftEdge[y_dict[axis]]/dy).astype(nT.Int64)
        return [xpoints, ypoints, fullProj.ravel(), toCombineMask.ravel(), weightProj.ravel()]

    def _set_myChildMask(self, newCM):
        if self.__myChildMask != None:
            mylog.warning("Overriding myChildMask attribute!  This is probably unwise!")
        self.__myChildMask = newCM

    def _set_myChildIndices(self, newCI):
        if self.__myChildIndices != None:
            mylog.warning("Overriding myChildIndices attribute!  This is probably unwise!")
        self.__myChildIndices = newCI

    def _get_myChildMask(self):
        if self.__myChildMask == None:
            self._generateChildMask()
        return self.__myChildMask

    def _get_myChildIndices(self):
        if self.__myChildIndices == None:
            self._generateChildMask()
        return self.__myChildIndices

    def _del_myChildIndices(self):
        print "YO mCI!"
        del self.__myChildIndices
        self.__myChildIndices = None

    def _del_myChildMask(self):
        pass

    #@time_execution
    def _generateChildMask(self):
        """
        Generates self.myChildMask, which is zero where child grids exist (and
        thus, where higher resolution data is available.)
        """
        self.__myChildMask = na.ones(self.ActiveDimensions, nT.Int32)
        LE = self.hierarchy.gridLeftEdge
        RE = self.hierarchy.gridRightEdge
        myChildrenGrids = na.where(
                      ( self.RightEdge[0] >= LE[:,0] )
                    & ( self.LeftEdge[0] <= RE[:,0]  )
                    & ( self.RightEdge[1] >= LE[:,1] )
                    & ( self.LeftEdge[1] <= RE[:,1]  )
                    & ( self.RightEdge[2] >= LE[:,2] )
                    & ( self.LeftEdge[2] <= RE[:,2]  )
                    & (self.hierarchy.gridLevels.ravel() == self.Level + 1) )
        for child in self.hierarchy.grids[myChildrenGrids]:
            if child.Level > self.Level + 1:
                continue
            # Now let's get our overlap
            si = [None]*3
            ei = [None]*3
            startIndex = ((child.LeftEdge - self.LeftEdge)/self.dx)
            endIndex = ((child.RightEdge - self.LeftEdge)/self.dx)
            for i in range(3):
                si[i] = na.rint(startIndex[i])
                ei[i] = na.rint(endIndex[i])
                si[i] = si[i]
                ei[i] = ei[i]
            self.__myChildMask[si[0]:ei[0], si[1]:ei[1], si[2]:ei[2]] = 0
        self.__myChildIndices = na.where(self.__myChildMask==0)

    def _get_coords(self):
        if self.myPrivateCoords == None: self._generateCoords()
        return self.myPrivateCoords

    def _set_coords(self, newC):
        if self.myPrivateCoords != None:
            mylog.warning("Overriding coords attribute!  This is probably unwise!")
        self.myPrivateCoords = newC

    def _del_coords(self):
        print "YO!"
        del self.myPrivateCoords
        self.myPrivateCoords = None

    def generateCoords(self):
        pass

    def _generateCoords(self):
        """
        Creates self.coords, which is of dimensions (3,ActiveDimensions)
        """
        #print "Generating coords"
        ind = na.indices(self.ActiveDimensions)
        LE = na.reshape(self.LeftEdge,(3,1,1,1))
        #print "Adding"
        self.myPrivateCoords = (ind+0.5)*self.dx+LE

    myPrivateCoords = None
    __myChildMask = None
    __myChildIndices = None

    coords = property(_get_coords, _get_coords, _del_coords)
    myChildMask = property(fget=_get_myChildMask, fdel=_del_myChildMask)
    myChildIndices = property(fget=_get_myChildIndices, fdel = _del_myChildIndices)

    def clearDerivedQuantities(self):
        """
        Clears coordinates, myChildIndices, myChildMask.
        """
        # Access the property raw-values here
        self.myChildMask = None
        self.myChildIndices = None
        self.coords = None

